import re
import unittest
from pathlib import Path

from wievac.pi.app import edge_result_v5 as v5


class EdgeResultV5CContractTests(unittest.TestCase):
    def setUp(self):
        self.header = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.h").read_text(encoding="utf-8")
        self.source = Path("wievac/firmware/receiver/main/edge_result_v5_pipeline.c").read_text(encoding="utf-8")
        self.formula = Path("wievac/firmware/receiver/main/formula_flex.c").read_text(encoding="utf-8")

    def test_wire_constants_match_python_codec(self):
        self.assertIn(f"EDGE_RESULT_V5_MAGIC UINT32_C(0x{v5.MAGIC:08X})", self.header)
        self.assertIn(f"EDGE_RESULT_V5_PROTOCOL_VERSION UINT8_C({v5.PROTOCOL_VERSION})", self.header)
        self.assertIn(f"EDGE_RESULT_V5_SCHEMA_VERSION UINT16_C({v5.SCHEMA_VERSION})", self.header)
        self.assertIn(f"EDGE_RESULT_V5_MESSAGE_TYPE UINT8_C(0x{v5.MESSAGE_TYPE_EDGE_RESULT:02X})", self.header)

    def test_callback_contract_is_bounded_and_nonblocking(self):
        self.assertIn("edge_result_pipeline_submit_csi", self.header)
        self.assertRegex(self.source, r"queue.*drop_count|drop_count.*queue")
        self.assertIn("EDGE_RESULT_REASON_WRONG_SOURCE", self.header)
        self.assertIn("EDGE_RESULT_REASON_INVALID_CSI", self.header)

    def test_stream_identity_and_window_contract_are_explicit(self):
        self.assertIn("uint32_t boot_id;", self.header)
        self.assertIn("edge_result_pipeline_reset_link_boot", self.header)
        self.assertIn("result->rx_timestamp_us < result->window_end_us", self.source)
        self.assertIn("result->invalid_count > result->sample_count", self.source)
        self.assertIn("expected_tx_mac == NULL", self.source)

    def test_c_formula_is_versioned_separately_from_python_reference(self):
        self.assertIn("formula-flex-v5.3-rx-median-mad", self.source)

    def test_score_filter_keeps_raw_evidence_separate_from_display_score(self):
        self.assertIn("EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY 5U", self.header)
        self.assertIn("EDGE_RESULT_V5_SCORE_FILTER_WALK_TAU 3.0f", self.source)
        self.assertIn("EDGE_RESULT_V5_SCORE_FILTER_CROWD_TAU 7.0f", self.source)
        self.assertNotIn("EDGE_RESULT_V5_SCORE_FILTER_ALPHA 0.30f", self.source)
        self.assertNotIn("EDGE_RESULT_V5_SCORE_FILTER_MAX_STEP 8.0f", self.source)
        self.assertIn("static float filter_passability_score", self.source)
        self.assertIn("static bool publish_filtered_score", self.source)
        self.assertIn("static bool emit_held_passability", self.source)
        self.assertIn("result.raw_evidence_score = result.formula_score", self.source)
        score_section = self.source.split("if (result.score_valid) {", 1)[1].split(
            "/* Every emitted result advances", 1
        )[0]
        self.assertIn("publish_filtered_score", score_section)
        self.assertIn("reset_score_filter(link)", self.source)
        self.assertIn("emit_held_passability(link, &result)", self.source)

    def test_fresh_clean_ready_baseline_is_not_stale_from_model_history(self):
        make_result = self.source.split("static edge_result_v5_t make_result", 1)[1].split(
            "static void update_link", 1
        )[0]
        stale_expression = make_result.split("const bool stale =", 1)[1].split(
            "if (stale ||", 1
        )[0]
        self.assertIn("(float)result.age_ms > pipeline->config.stale_after_ms", stale_expression)
        self.assertNotIn("baseline_history_stale", stale_expression)
        self.assertNotIn("temporal_history_stale", stale_expression)
        self.assertNotIn("temporal_history_stale", make_result)
        self.assertIn("EDGE_RESULT_REASON_STALE", make_result)
        self.assertIn("const edge_result_formula_fn formula", make_result)
        self.assertIn("formula(&result.features, link", make_result)
        self.assertIn("result.invalid_count > 0U", make_result)
        self.assertIn("result.queue_drop_count > 0U", make_result)
        self.assertIn("result.sequence_gap > 0U", make_result)

    def test_gap_window_remains_fail_closed_and_cannot_train_bootstrap(self):
        make_result = self.source.split("static edge_result_v5_t make_result", 1)[1].split(
            "static void update_link", 1
        )[0]
        bootstrap_gate = make_result.split("const bool bootstrap_window_eligible", 1)[1].split(
            "if (stale ||", 1
        )[0]
        self.assertIn("!link->baseline_ready", bootstrap_gate)
        self.assertIn("result.invalid_count == 0U", bootstrap_gate)
        self.assertIn("result.queue_drop_count == 0U", bootstrap_gate)
        self.assertIn("result.sequence_gap == 0U", bootstrap_gate)
        self.assertIn("link->baseline_window_eligible", bootstrap_gate)
        self.assertNotIn("EDGE_RESULT_V5_BOOTSTRAP_MAX_LOSS_RATIO", bootstrap_gate)
        self.assertIn("result.sequence_gap > 0U || !link->baseline_ready", make_result)
        self.assertIn("EDGE_RESULT_REASON_SEQUENCE_GAP", make_result)
        self.assertNotIn("bootstrap_bounded_loss", make_result)

    def test_isolated_sequence_gap_does_not_erase_bootstrap_history(self):
        update_body = self.source.split("static void update_link", 1)[1].split(
            "void edge_result_pipeline_config_defaults", 1
        )[0]
        gap_branch = update_body.split("if (delta > 1U)", 1)[1].split(
            "if (link->last_timestamp_us", 1
        )[0]
        self.assertIn("link->baseline_window_eligible = false", gap_branch)
        self.assertIn("link->previous_amplitude_valid = false", gap_branch)
        self.assertNotIn("baseline_count = 0U", gap_branch)

        reset_body = self.source.split("static void reset_window", 1)[1].split(
            "static void sync_baseline_metadata", 1
        )[0]
        self.assertIn("EDGE_RESULT_V5_BOOTSTRAP_MAX_LOSS_RATIO", reset_body)
        self.assertIn("EDGE_RESULT_V5_SEVERE_WINDOW_TOLERANCE", reset_body)
        self.assertIn("baseline_count = 0U", reset_body)
        self.assertIn("candidate_cancelled_invalid_window", reset_body)
        gap_pause = reset_body.split("else if (link->window_sequence_gap > 0U)", 1)[1].split(
            "} else {", 1
        )[0]
        self.assertIn("bootstrap_paused_sequence_gap", gap_pause)
        self.assertIn("pause_rebase_candidate(link)", gap_pause)

    def test_c_lifecycle_is_explicitly_reference_only(self):
        self.assertIn('EDGE_RESULT_V5_C_FORMULA_STATUS "REFERENCE_ONLY"', self.header)
        self.assertIn('EDGE_RESULT_V5_C_PARITY_STATUS "UNVERIFIED"', self.header)
        self.assertIn('EDGE_RESULT_V5_C_ACTIVE_FIRMWARE_STATUS "NOT_READY"', self.header)
        active_source = Path("wievac/firmware/receiver/main/app_main.c").read_text(encoding="utf-8")
        self.assertIn('edge_result_v5_pipeline.h', active_source)

    def test_reference_thresholds_are_named_and_isolated(self):
        self.assertIn("EDGE_RESULT_V5_C_REFERENCE_BASELINE_DELTA_MOTION", self.source)
        self.assertIn("EDGE_RESULT_V5_C_REFERENCE_SCORE_BLOCK", self.source)
        self.assertNotRegex(self.source, r"baseline_delta\s*[<>]=?\s*[48]\.0f")

    def test_v4_source_is_not_replaced(self):
        source = Path("wievac/firmware/receiver/main/app_main.c").read_text(encoding="utf-8")
        self.assertIn("V4_MAGIC", source)
        self.assertIn("V4_MSG_DYNAMIC_FRAME", source)

    def test_single_invalid_preserves_bootstrap_history_and_counts_once(self):
        invalid_body = self.source.split("static void attribute_invalid_frame", 1)[1].split(
            "static const edge_result_link_state_t *find_link_const", 1
        )[0]
        self.assertNotIn("baseline_count = 0U", invalid_body)
        self.assertIn("link->baseline_window_eligible = false", invalid_body)
        self.assertIn("link->previous_amplitude_valid = false", invalid_body)
        self.assertIn("Pending counters belong to the next window", self.source)
        self.assertIn("link->window_sample_count = pending_invalid_count", self.source)
        self.assertIn("link->window_invalid_count = pending_invalid_count", self.source)
        self.assertIn("EDGE_RESULT_V5_SEVERE_WINDOW_TOLERANCE", self.source)

    def test_transport_faults_cut_amplitude_continuity(self):
        submit_body = self.source.split("bool edge_result_pipeline_submit_csi", 1)[1].split(
            "bool edge_result_pipeline_process_one", 1
        )[0]
        queue_drop = submit_body.split("if (!queue_push", 1)[1].split("return false;", 1)[0]
        self.assertIn("++link->queue_drop_total", queue_drop)
        self.assertIn("link->previous_amplitude_valid = false", queue_drop)
        self.assertIn("link->baseline_window_eligible = false", queue_drop)

        update_body = self.source.split("static void update_link", 1)[1].split(
            "void edge_result_pipeline_config_defaults", 1
        )[0]
        duplicate_reject = update_body.split("if (delta == 0U", 1)[1].split("return;", 1)[0]
        timestamp_reject = update_body.split("record->timestamp_us <= link->last_timestamp_us", 1)[1].split(
            "return;", 1
        )[0]
        self.assertIn("link->previous_amplitude_valid = false", duplicate_reject)
        self.assertIn("link->previous_amplitude_valid = false", timestamp_reject)

    def test_rebase_pause_requires_fresh_clean_evidence(self):
        self.assertIn("pause_rebase_candidate(link)", self.source)
        self.assertIn("EDGE_RESULT_V5_LIGHT_PAUSE_TOLERANCE", self.source)
        self.assertIn("rebase_candidate_start_us = 0U", self.source)
        self.assertIn("rebase_candidate_stable_windows >= required_candidate_count", self.source)
        self.assertIn("rebase_candidate_pause_windows == 0U", self.source)
        self.assertIn("candidate_cancelled_contradictory_evidence", self.source)
        self.assertIn("candidate_cancelled_repeated_quality_window", self.source)
        self.assertIn("rebase_candidate_pause_windows != 0U", self.formula)

    def test_rebase_contradiction_is_independent_of_aggregate_displacement(self):
        predicate = self.source.split("const bool strong_contradiction", 1)[1].split(
            "if (strong_contradiction &&", 1
        )[0]
        self.assertIn("doppler_walking", predicate)
        self.assertNotIn("normalized_spread >= occupancy_z", predicate)
        self.assertIn("link->occupancy_memory_windows > 0U", predicate)
        self.assertNotIn("result.blocking_evidence", predicate)

        candidate_gate = self.source.split("const bool candidate_environment_evidence", 1)[1].split(
            "if (!candidate_environment_evidence)", 1
        )[0]
        self.assertNotIn("result.blocking_evidence", candidate_gate)
        self.assertNotIn("normalized_spread", candidate_gate)
        self.assertIn("result.blocking_evidence >= blocking_gate", self.source)

    def test_level_shift_alone_cannot_promote_occupancy(self):
        shift_branch = self.source.split("const bool level_shift", 1)[1].split(
            "const uint64_t shift_elapsed_us", 1
        )[0]
        self.assertIn("independent_blocking_signal", shift_branch)
        self.assertIn("doppler_walking", shift_branch)
        self.assertNotIn("normalized_spread >= occupancy_z", shift_branch)
        self.assertNotIn("normalized_level >= occupancy_z) {", shift_branch)
        self.assertNotIn("blocking_stress = normalized_level", self.source)

    def test_occupancy_signal_is_temporal_motion_not_csi_shape(self):
        occupancy = self.source.split("const bool independent_blocking_signal =", 1)[1].split(
            "if (independent_blocking_signal)", 1
        )[0]
        self.assertIn("doppler_walking", occupancy)
        self.assertIn("temporal_motion >= occupancy_z", occupancy)
        self.assertNotIn("normalized_spread", occupancy)
        self.assertNotIn("robust_spread", occupancy)
        formula = self.formula.split("float formula_flex_default_formula", 1)[1].split(
            "void formula_flex_update_baseline", 1
        )[0]
        self.assertIn("excess_motion", formula)
        self.assertIn("deviation", formula)
        self.assertNotIn("excess_spread", formula)

    def test_doppler_gates_occupancy_not_published_score(self):
        make_result = self.source.split("static edge_result_v5_t make_result", 1)[1].split(
            "static void update_link", 1
        )[0]
        self.assertNotIn("result.doppler_ratio * stress", make_result)
        self.assertIn("result.raw_evidence_score = result.formula_score", make_result)
        self.assertIn("learn_doppler_ratio_scale", make_result)
        self.assertIn("doppler_walking", make_result)
        self.assertIn("doppler_ratio_scale", make_result)
        self.assertNotIn("result.doppler_ratio * stress", self.formula)
        self.assertIn("features->temporal_motion - 1.0f", self.formula)

    def test_gated_score_and_dual_hypothesis_baseline_are_present(self):
        self.assertIn("EDGE_RESULT_V5_BOOTSTRAP_QUIET_WINDOWS 12U", self.source)
        self.assertIn("EDGE_RESULT_V5_REBASE_QUIET_WINDOWS 12U", self.source)
        self.assertIn("EDGE_RESULT_V5_STUCK_LOW_WINDOWS 12U", self.source)
        self.assertIn("EDGE_RESULT_V5_OCCUPANCY_MEMORY_WINDOWS 25U", self.source)
        self.assertIn("occupancy_memory_windows", self.header)
        self.assertIn("publish_filtered_score(link, &result, corroborated, occupied_memory)", self.source)
        kconfig = Path("wievac/firmware/receiver/main/Kconfig.projbuild").read_text(encoding="utf-8")
        self.assertIn("range 1 64", kconfig)
        service = Path("wievac/pi/app/edge_result_v5_service.py").read_text(encoding="utf-8")
        self.assertIn("FIRMWARE_RX_MAX = 64", service)

    def test_packet_loss_compatibility_slot_is_csi_only(self):
        make_result = self.source.split("static edge_result_v5_t make_result", 1)[1].split(
            "static void update_link", 1
        )[0]
        self.assertIn("const uint32_t csi_sequence_total", make_result)
        self.assertIn("result.sequence_gap /", make_result)
        self.assertIn("not UDP loss", self.header)
        ratio_body = make_result.split("const uint32_t csi_sequence_total", 1)[1].split(
            "if (link->window_interval_count", 1
        )[0]
        self.assertNotIn("result.queue_drop_count", ratio_body)

    def test_receiver_status_keeps_measurement_csi_and_udp_sources_separate(self):
        app_source = Path("wievac/firmware/receiver/main/app_main.c").read_text(encoding="utf-8")
        status = app_source.split("static void log_receiver_status", 1)[1]
        for field in (
            "measurement_received", "measurement_lost", "measurement_sequence_gap",
            "csi_sequence_gaps", "csi_context_rejects", "csi_queue_drops",
            "invalid_csi", "udp_queue_drops", "udp_send_failures",
        ):
            self.assertIn(field, status)
        context_body = app_source.split("static bool measurement_context_admissible", 1)[1].split(
            "static void espnow_receive_callback", 1
        )[0]
        self.assertNotIn("g_measurement_reorders", context_body)

    def test_v6_pipeline_owns_window_emission_without_periodic_partial_flush(self):
        app_source = Path("wievac/firmware/receiver/main/app_main.c").read_text(encoding="utf-8")
        v6_submit = app_source.split("static void v6_submit_and_emit", 1)[1].split(
            "#endif", 1
        )[0]
        self.assertIn("edge_result_pipeline_process_one", v6_submit)
        self.assertIn("while (edge_result_pipeline_process_one", v6_submit)

        emit_feature = app_source.split("static void emit_feature", 1)[1].split(
            "#else", 1
        )[0]
        self.assertIn("#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE", emit_feature)
        self.assertIn("reset_window(state, end_us)", emit_feature)
        self.assertNotIn("edge_result_pipeline_flush_link", emit_feature)
        self.assertIn("#else", app_source.split("static void emit_feature", 1)[1])
        self.assertIn("V4_MSG_DYNAMIC_FRAME", app_source)


if __name__ == "__main__":
    unittest.main()
