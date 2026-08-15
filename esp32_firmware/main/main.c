#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

#define TAG "WiFiSenseUltra"
#define WIFI_READY_BIT BIT0
#define CSI_MAX_BYTES 384
#define TX_BUFFER_BYTES 4096
#define CSI_QUEUE_DEPTH 48

static EventGroupHandle_t wifi_events;
static QueueHandle_t csi_queue;
static volatile uint32_t dropped_frames = 0;
static int64_t last_csi_us = 0;

typedef struct {
    int8_t buf[CSI_MAX_BYTES];
    uint16_t len;
    int8_t rssi;
    uint8_t rate;
    int64_t timestamp_us;
} csi_item_t;

static bool send_all(int sock, const char *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        int n = send(sock, buf + sent, len - sent, 0);
        if (n <= 0) return false;
        sent += (size_t)n;
    }
    return true;
}

/* ESP-IDF invokes this callback from the Wi-Fi task, not a hardware ISR.
 * Keep it short: copy validated bytes to a queue and return immediately. */
static void csi_callback(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf || !csi_queue) return;

    const int target_hz = CONFIG_WIFISENSE_TARGET_HZ > 0 ? CONFIG_WIFISENSE_TARGET_HZ : 20;
    const int64_t min_interval_us = 1000000LL / target_hz;
    const int64_t now = esp_timer_get_time();
    if (last_csi_us && (now - last_csi_us) < min_interval_us) return;
    last_csi_us = now;

    uint16_t offset = info->first_word_invalid ? 4 : 0;
    if (info->len <= offset + 3) return;
    uint16_t valid_len = info->len - offset;
    if (valid_len > CSI_MAX_BYTES) valid_len = CSI_MAX_BYTES;
    valid_len &= ~1U;  /* CSI is imag,real pairs. */

    csi_item_t item = {0};
    item.len = valid_len;
    memcpy(item.buf, info->buf + offset, valid_len);
    item.rssi = info->rx_ctrl.rssi;
    item.rate = info->rx_ctrl.rate;
    item.timestamp_us = now;

    if (xQueueSend(csi_queue, &item, 0) != pdTRUE) {
        dropped_frames++;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, WIFI_READY_BIT);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_events, WIFI_READY_BIT);
    }
}

static void wifi_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    wifi_config_t wifi_cfg = {0};
    strlcpy((char *)wifi_cfg.sta.ssid, CONFIG_WIFISENSE_WIFI_SSID, sizeof(wifi_cfg.sta.ssid));
    strlcpy((char *)wifi_cfg.sta.password, CONFIG_WIFISENSE_WIFI_PASSWORD, sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static void csi_init(void) {
    wifi_csi_config_t cfg = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = true,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    /* Espressif recommends promiscuous mode when more CSI traffic is needed. */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
}

static int connect_server(void) {
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(CONFIG_WIFISENSE_SERVER_PORT);
    if (inet_pton(AF_INET, CONFIG_WIFISENSE_SERVER_IP, &addr.sin_addr) != 1) return -1;

    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (sock < 0) return -1;

    int keepalive = 1, idle = 15, interval = 5, count = 3;
    setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &keepalive, sizeof(keepalive));
#ifdef TCP_KEEPIDLE
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof(idle));
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPINTVL, &interval, sizeof(interval));
    setsockopt(sock, IPPROTO_TCP, TCP_KEEPCNT, &count, sizeof(count));
#endif

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(sock);
        return -1;
    }

    char hello[192];
    int n;
    if (strlen(CONFIG_WIFISENSE_INGEST_TOKEN) > 0) {
        n = snprintf(hello, sizeof(hello), "NODE|%s|%s\n",
                     CONFIG_WIFISENSE_NODE_ID, CONFIG_WIFISENSE_INGEST_TOKEN);
    } else {
        n = snprintf(hello, sizeof(hello), "NODE|%s\n", CONFIG_WIFISENSE_NODE_ID);
    }
    if (n <= 0 || !send_all(sock, hello, (size_t)n)) {
        close(sock);
        return -1;
    }
    return sock;
}

static void sender_task(void *arg) {
    char packet[TX_BUFFER_BYTES];
    int retry_ms = 1000;

    while (true) {
        xEventGroupWaitBits(wifi_events, WIFI_READY_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        int sock = connect_server();
        if (sock < 0) {
            ESP_LOGW(TAG, "Server connect failed; retry in %d ms", retry_ms);
            vTaskDelay(pdMS_TO_TICKS(retry_ms));
            retry_ms = retry_ms < 30000 ? retry_ms * 2 : 30000;
            continue;
        }
        retry_ms = 1000;
        ESP_LOGI(TAG, "Connected to %s:%d as %s", CONFIG_WIFISENSE_SERVER_IP,
                 CONFIG_WIFISENSE_SERVER_PORT, CONFIG_WIFISENSE_NODE_ID);

        csi_item_t item;
        while ((xEventGroupGetBits(wifi_events) & WIFI_READY_BIT) != 0) {
            if (xQueueReceive(csi_queue, &item, pdMS_TO_TICKS(1000)) != pdTRUE) continue;

            int pos = snprintf(packet, sizeof(packet), "CSI|%lld|%d|%u|%u|",
                               (long long)item.timestamp_us, item.rssi, item.rate, item.len);
            if (pos <= 0 || pos >= (int)sizeof(packet)) break;

            for (uint16_t i = 0; i < item.len; i++) {
                int n = snprintf(packet + pos, sizeof(packet) - (size_t)pos,
                                 "%d%s", item.buf[i], (i + 1 == item.len) ? "\n" : ",");
                if (n <= 0 || n >= (int)(sizeof(packet) - (size_t)pos)) {
                    pos = -1;
                    break;
                }
                pos += n;
            }
            if (pos <= 0 || !send_all(sock, packet, (size_t)pos)) {
                ESP_LOGW(TAG, "TCP send failed: errno=%d", errno);
                break;
            }
        }
        shutdown(sock, SHUT_RDWR);
        close(sock);
    }
}

void app_main(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    } else {
        ESP_ERROR_CHECK(ret);
    }

    wifi_events = xEventGroupCreate();
    csi_queue = xQueueCreate(CSI_QUEUE_DEPTH, sizeof(csi_item_t));
    if (!wifi_events || !csi_queue) {
        ESP_LOGE(TAG, "Failed to allocate runtime objects");
        return;
    }

    ESP_LOGI(TAG, "WiFiSense Ultra node starting: %s", CONFIG_WIFISENSE_NODE_ID);
    wifi_init();
    xEventGroupWaitBits(wifi_events, WIFI_READY_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
    csi_init();
    xTaskCreate(sender_task, "csi_sender", 6144, NULL, 5, NULL);

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        ESP_LOGI(TAG, "queue=%u dropped=%lu", (unsigned)uxQueueMessagesWaiting(csi_queue),
                 (unsigned long)dropped_frames);
    }
}
