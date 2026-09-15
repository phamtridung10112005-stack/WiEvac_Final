import unittest

from wievac.pi.app.edge_result_v5_dashboard import dashboard_routes, render_dashboard_html


class EdgeResultV5DashboardTests(unittest.TestCase):
    def setUp(self):
        self.html = render_dashboard_html()

    def test_dynamic_cards_and_metrics(self):
        self.assertIn("function ensureCards", self.html)
        self.assertIn("id=\"card-template\"", self.html)
        self.assertIn("data-stability>", self.html)
        self.assertIn("data-gap>", self.html)
        self.assertIn("data-queue-drop>", self.html)
        self.assertIn("data-invalid>", self.html)
        self.assertIn("data-score>", self.html)
        self.assertIn("class=\"state-text\"", self.html)
        self.assertNotIn("HÀNH LANG 1 — RX-1", self.html)
        self.assertNotIn("HÀNH LANG 2 — RX-2", self.html)
        self.assertNotIn("id=\"card-rx-1\"", self.html)
        self.assertNotIn("Link link-1", self.html)

    def test_single_refresh_and_required_status_mapping(self):
        self.assertEqual(self.html.count("fetch('/api/v5/overview'"), 1)
        self.assertEqual(self.html.count("setInterval(refresh,1000)"), 1)
        self.assertNotIn("liveRefresh", self.html)
        self.assertNotIn("liveUpdate", self.html)
        for label in (
            "CHƯA NHẬN PACKET",
            "ĐANG HỌC DỮ LIỆU",
            "TRỐNG/PASSABLE",
            "DEGRADED/CROWDED",
            "KHÓ ĐI QUA",
            "DỮ LIỆU KHÔNG ỔN ĐỊNH",
            "MẤT KẾT NỐI",
        ):
            self.assertIn(label, self.html)
        self.assertIn("CSI gap trung bình", self.html)
        self.assertIn("Mất UDP", self.html)
        self.assertIn("data-udp-loss", self.html)
        self.assertIn("udp_loss_status", self.html)
        self.assertIn("pi_ingest", self.html)
        self.assertIn("CSI gap", self.html)
        self.assertIn("Queue drop", self.html)
        self.assertIn("sequence_gap", self.html)
        self.assertIn("queue_drop_count", self.html)
        self.assertIn("invalid_count", self.html)
        self.assertIn("data-invalid", self.html)
        self.assertIn("csi_gap_mean", self.html)
        self.assertIn("commitDisplayState", self.html)
        self.assertIn("passabilityFromScore", self.html)
        self.assertIn("score < 20", self.html)
        self.assertIn("count>=3", self.html)
        self.assertIn("displayState", self.html)
        self.assertNotIn("data-loss", self.html)
        self.assertNotIn("Mất gói trung bình", self.html)
        self.assertIn("arrival_timestamp_us!==null", self.html)
        self.assertNotIn("accepted_count)||", self.html)
        self.assertIn("/api/v5/nodes", self.html)
        self.assertIn("/api/v5/labels", self.html)
        self.assertIn("Trống", self.html)
        self.assertIn("Có người", self.html)
        self.assertIn("Không rõ", self.html)
        self.assertIn("data-occupancy", self.html)
        self.assertIn("/api/v5/recorder", self.html)
        self.assertIn("Bắt đầu ghi", self.html)
        self.assertIn("Dừng ghi", self.html)
        self.assertIn("Đặt lịch thu", self.html)
        self.assertIn("datetime-local", self.html)
        self.assertIn("Hành lang trống", self.html)
        self.assertIn("class=\"masthead\"", self.html)

    def test_utf8_and_no_mojibake(self):
        self.assertIn('charset="utf-8"', self.html)
        for label in ("ĐANG CHẠY", "Cập nhật gần nhất", "CSI gap trung bình", "Chi tiết kỹ thuật"):
            self.assertIn(label, self.html)
        for fragment in ("\u00c3\u0192", "\u00c4\u0090ang", "\u00c3\u00a1\u00c2\u00bb"):
            self.assertNotIn(fragment, self.html)
        self.assertNotIn("raw_csi", self.html)
        self.assertNotIn("/api/v5/trends", self.html)
        self.assertNotIn("/api/v5/route", self.html)

    def test_enterprise_shell_has_wall_and_manage(self):
        self.assertIn("Tường", self.html)
        self.assertIn("Quản lý", self.html)
        self.assertIn("Thêm node", self.html)
        self.assertIn("Cần chú ý", self.html)
        self.assertIn("/static/utehy-logo.png", self.html)
        self.assertIn("TRƯỜNG ĐẠI HỌC SƯ PHẠM KỸ THUẬT HƯNG YÊN", self.html)
        self.assertIn("Đội thực hiện", self.html)

    def test_routes_preserve_overview_contract(self):
        routes = dashboard_routes()
        self.assertEqual(routes["/api/v5/overview"], "overview")
        self.assertEqual(routes["/api/v5/nodes"], "nodes")
        self.assertEqual(routes["/api/v5/labels"], "labels")
        self.assertEqual(routes["/api/v5/recorder"], "recorder")
        self.assertNotIn("/api/v5/trends", routes)


if __name__ == "__main__":
    unittest.main()
