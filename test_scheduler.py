import calendar
import csv
import unittest
from unittest.mock import MagicMock, patch

# main.py からクラスや定数をインポート
import main
from main import ShiftSchedulerApp, DEFAULT_STAFFS, SHIFT_TYPES

class TestShiftScheduler(unittest.TestCase):
    def setUp(self):
        # Tkinterのルートウィンドウを初期化（非表示）
        self.app = ShiftSchedulerApp()
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def test_default_fallback(self):
        """CSV未インポート時にデフォルト16名が保持されているか検証"""
        self.assertEqual(len(self.app.staff_list), 16)
        self.assertEqual(self.app.staff_list[0]["name"], "スタッフ01")
        self.assertEqual(self.app.staff_list[0]["is_leader"], 1)
        self.assertEqual(self.app.staff_list[0]["preferred_holidays"], [])

    def test_csv_import_success(self):
        """sample_staffs.csv を正しくインポートできるか検証"""
        with patch("tkinter.filedialog.askopenfilename", return_value="sample_staffs.csv"), \
             patch("tkinter.messagebox.showinfo") as mock_info:
            self.app.import_staff_csv()

            mock_info.assert_called_once()
            self.assertEqual(len(self.app.staff_list), 16)
            
            # スタッフ1: 佐藤 健 (リーダー, 希望休: [3, 10, 18])
            s0 = self.app.staff_list[0]
            self.assertEqual(s0["name"], "佐藤 健")
            self.assertEqual(s0["is_leader"], 1)
            self.assertEqual(s0["is_rookie"], 0)
            self.assertEqual(s0["preferred_holidays"], [3, 10, 18])

            # スタッフ11: 佐々木 結衣 (一般, 希望休なし)
            s10 = self.app.staff_list[10]
            self.assertEqual(s10["name"], "佐々木 結衣")
            self.assertEqual(s10["is_leader"], 0)
            self.assertEqual(s10["is_rookie"], 0)
            self.assertEqual(s10["preferred_holidays"], [])

            # スタッフ13: 松本 陽菜 (新人, 希望休: [5, 12])
            s12 = self.app.staff_list[12]
            self.assertEqual(s12["name"], "松本 陽菜")
            self.assertEqual(s12["is_leader"], 0)
            self.assertEqual(s12["is_rookie"], 1)
            self.assertEqual(s12["preferred_holidays"], [5, 12])

            # ラベル表示の更新確認
            self.assertIn("16名 (CSV読込済)", self.app.lbl_staff_status.cget("text"))

    def test_csv_import_shift_jis(self):
        """Shift-JIS(cp932)エンコーディングのCSVも正常に読み込めるか検証"""
        sjis_filename = "test_sjis.csv"
        csv_text = "スタッフ名,役職,希望休\n田中,リーダー,\"1,2\"\n高橋,一般,\"3,4\"\n"
        with open(sjis_filename, "w", encoding="cp932") as f:
            f.write(csv_text)

        try:
            with patch("tkinter.filedialog.askopenfilename", return_value=sjis_filename), \
                 patch("tkinter.messagebox.showinfo"):
                self.app.import_staff_csv()

            self.assertEqual(len(self.app.staff_list), 2)
            self.assertEqual(self.app.staff_list[0]["name"], "田中")
            self.assertEqual(self.app.staff_list[0]["preferred_holidays"], [1, 2])
            self.assertEqual(self.app.staff_list[1]["name"], "高橋")
            self.assertEqual(self.app.staff_list[1]["preferred_holidays"], [3, 4])
        finally:
            import os
            if os.path.exists(sjis_filename):
                os.remove(sjis_filename)

    def test_csv_import_cancel_preserves_state(self):
        """ファイルダイアログでキャンセルした際に既存のスタッフが維持されるか検証"""
        original_staffs = [dict(s) for s in self.app.staff_list]
        with patch("tkinter.filedialog.askopenfilename", return_value=""):
            self.app.import_staff_csv()
            self.assertEqual(self.app.staff_list, original_staffs)

    def test_optimization_with_preferred_holidays(self):
        """最適化を実行し、希望休が確実に公休になっているか、全制約が充足されているか検証"""
        # CSVをインポート
        with patch("tkinter.filedialog.askopenfilename", return_value="sample_staffs.csv"), \
             patch("tkinter.messagebox.showinfo"):
            self.app.import_staff_csv()

        # 2026年10月(31日)で最適化実行
        self.app.year_entry.delete(0, "end")
        self.app.year_entry.insert(0, "2026")
        self.app.month_entry.delete(0, "end")
        self.app.month_entry.insert(0, "10")

        with patch("tkinter.messagebox.showinfo") as mock_success, \
             patch("tkinter.messagebox.showwarning") as mock_fail:
            self.app.run_optimization()
            self.assertTrue(mock_success.called, "最適化が成功するはずですが失敗しました")

        schedule = self.app.schedule_result
        num_days = calendar.monthrange(2026, 10)[1]

        # 1. 希望休が全て「公休」になっているか検証
        for s_idx, staff in enumerate(self.app.staff_list):
            for d in staff.get("preferred_holidays", []):
                if 1 <= d <= num_days:
                    assigned = schedule.get((s_idx, d))
                    self.assertEqual(
                        assigned, "公休",
                        f"{staff['name']} の希望休 {d}日 が公休になっていません（実際: {assigned}）"
                    )

        # 2. 日勤最低3名、夜勤最低2名（上限3名）の制約チェック
        for d in range(1, num_days + 1):
            nikkin_count = sum(1 for s in range(len(self.app.staff_list)) if schedule.get((s, d)) == "日勤")
            yakin_count = sum(1 for s in range(len(self.app.staff_list)) if schedule.get((s, d)) == "夜勤")
            self.assertGreaterEqual(nikkin_count, 3, f"{d}日の日勤人数が最低3人を下回っています ({nikkin_count})")
            self.assertGreaterEqual(yakin_count, 2, f"{d}日の夜勤人数が最低2人を下回っています ({yakin_count})")
            self.assertLessEqual(yakin_count, 3, f"{d}日の夜勤人数が上限3人を超えています ({yakin_count})")

        # 3. 夜勤の翌日は必ず「明け」
        for s in range(len(self.app.staff_list)):
            for d in range(1, num_days):
                if schedule.get((s, d)) == "夜勤":
                    next_shift = schedule.get((s, d + 1))
                    self.assertEqual(
                        next_shift, "明け",
                        f"スタッフ{s} の {d}日夜勤の翌日({d+1}日)が明けではありません ({next_shift})"
                    )

        # 4. 夜勤に新人同士の配置NG
        for d in range(1, num_days + 1):
            night_rookies = [
                s for s in range(len(self.app.staff_list))
                if schedule.get((s, d)) == "夜勤" and self.app.staff_list[s]["is_rookie"]
            ]
            self.assertLessEqual(len(night_rookies), 1, f"{d}日の夜勤に新人が2名配置されています")

        # 5. 総勤務時間の平準化検証（全スタッフが基準労働時間 168h の ±8h 以内であること）
        SHIFT_HOURS = {"日勤": 8, "夜勤": 16, "明け": 0, "公休": 0}
        staff_total_hours = [
            sum(SHIFT_HOURS.get(schedule.get((s, d)), 0) for d in range(1, num_days + 1))
            for s in range(len(self.app.staff_list))
        ]
        min_h = min(staff_total_hours)
        max_h = max(staff_total_hours)
        diff_h = max_h - min_h
        print(f"\n[検証結果] 全スタッフ総勤務時間: {staff_total_hours} (Min: {min_h}h, Max: {max_h}h, 差: {diff_h}h)")

        # 基準労働時間（168h）の±8h以内（160h〜176h）であることを検証
        target_std_hours = 168
        for s_idx, h in enumerate(staff_total_hours):
            self.assertTrue(
                target_std_hours - 8 <= h <= target_std_hours + 8,
                f"スタッフ{s_idx} の勤務時間 {h}h が基準時間 {target_std_hours}h の±8h以内に収まっていません"
            )

        # 最大差が8時間以内であることを検証
        self.assertLessEqual(diff_h, 8, f"最大勤務時間と最小勤務時間の差が8hを超えています: {diff_h}h")

    def test_optimization_default_staffs(self):
        """CSV未インポートの初期16名でも基準労働時間(152h)±8h以内で最適化できるか検証"""
        self.app.year_entry.delete(0, "end")
        self.app.year_entry.insert(0, "2026")
        self.app.month_entry.delete(0, "end")
        self.app.month_entry.insert(0, "11")

        with patch("tkinter.messagebox.showinfo") as mock_success, \
             patch("tkinter.messagebox.showwarning") as mock_fail:
            self.app.run_optimization()
            self.assertTrue(mock_success.called, "最適化が成功するはずですが失敗しました")

        schedule = self.app.schedule_result
        num_days = calendar.monthrange(2026, 11)[1]
        SHIFT_HOURS = {"日勤": 8, "夜勤": 16, "明け": 0, "公休": 0}
        staff_total_hours = [
            sum(SHIFT_HOURS.get(schedule.get((s, d)), 0) for d in range(1, num_days + 1))
            for s in range(len(self.app.staff_list))
        ]
        min_h = min(staff_total_hours)
        max_h = max(staff_total_hours)
        diff_h = max_h - min_h
        print(f"\n[デフォルトスタッフ検証結果 11月] 全スタッフ総勤務時間: {staff_total_hours} (Min: {min_h}h, Max: {max_h}h, 差: {diff_h}h)")

        target_std_hours = 152
        for s_idx, h in enumerate(staff_total_hours):
            self.assertTrue(
                target_std_hours - 8 <= h <= target_std_hours + 8,
                f"スタッフ{s_idx} の勤務時間 {h}h が基準時間 {target_std_hours}h の±8h以内に収まっていません"
            )
        self.assertLessEqual(diff_h, 8)

    def test_standard_hours_calculation(self):
        """基準労働時間の自動計算・表示を検証"""
        # 2026年10月（31日、土日祝10日 -> 実働21日 × 8h = 168h）
        self.app.year_entry.delete(0, "end")
        self.app.year_entry.insert(0, "2026")
        self.app.month_entry.delete(0, "end")
        self.app.month_entry.insert(0, "10")
        self.app.update_standard_hours()

        std_text = self.app.lbl_standard_hours.cget("text")
        self.assertIn("168h", std_text)
        self.assertIn("実働21日", std_text)
        self.assertIn("公休10日", std_text)

        alloc_text = self.app.lbl_target_allocation.cget("text")
        self.assertIn("168h", alloc_text)
        self.assertIn("±8h以内", alloc_text)

        # 2026年11月（30日、土日9日 + 祝日2日[文化の日・勤労感謝の日] = 公休11日 -> 実働19日 × 8h = 152h）
        self.app.month_entry.delete(0, "end")
        self.app.month_entry.insert(0, "11")
        self.app.update_standard_hours()

        std_text_nov = self.app.lbl_standard_hours.cget("text")
        self.assertIn("152h", std_text_nov)
        self.assertIn("実働19日", std_text_nov)
        self.assertIn("公休11日", std_text_nov)
        alloc_text_nov = self.app.lbl_target_allocation.cget("text")
        self.assertIn("152h", alloc_text_nov)
        self.assertIn("±8h以内", alloc_text_nov)

if __name__ == "__main__":
    unittest.main()
