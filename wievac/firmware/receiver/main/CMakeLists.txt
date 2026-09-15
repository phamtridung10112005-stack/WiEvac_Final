idf_component_register(SRCS "app_main.c"
                       "edge_result_v5_pipeline.c"
                       "noise_filter.c"
                       "formula_flex.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_event esp_netif esp_wifi esp_timer
                                nvs_flash esp_system lwip freertos)
