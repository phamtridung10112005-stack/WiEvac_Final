idf_component_register(SRCS "app_main.c"
                       "edge_result_v5_pipeline.c"
                       "noise_filter.c"
                       "formula_flex.c"
                       "tiny_ai_features.c"
                       "tiny_ai_gatekeeper.c"
                       "dfplayer_mini.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_event esp_netif esp_wifi esp_timer
                                nvs_flash esp_system lwip freertos driver)

# No FMA (madd.s) contraction, so Tiny AI matches the Python reference bit for bit.
set_source_files_properties(tiny_ai_features.c tiny_ai_gatekeeper.c
                            PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
