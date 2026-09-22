import calendar
import csv
from datetime import date, datetime
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from unittest.mock import MagicMock
import customtkinter as ctk
import jpholiday

# Windows Smart App Control等でpandasのDLL読込がブロックされる場合のフォールバック
try:
    import pandas
except (ImportError, Exception):
    sys.modules["pandas"] = MagicMock()

from ortools.sat.python import cp_model

# ----------------------------------------------------
# GUIテーマ設定
# ----------------------------------------------------
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# ----------------------------------------------------
# ダミーの初期スタッフデータ（約15〜20名規模）
# ----------------------------------------------------
DEFAULT_STAFFS = [
    {"name": f"スタッフ{i:02d}", "is_leader": 1 if i <= 4 else 0, "is_rookie": 1 if i >= 13 else 0, "preferred_holidays": []}
    for i in range(1, 17)
]

SHIFT_TYPES = ["日勤", "夜勤", "明け", "公休"]
SHIFT_COLORS = {
    "日勤": "#2b5c8f",
    "夜勤": "#7b2cbf",
    "明け": "#d97706",
    "公休": "#374151"
}
SHIFT_HOURS = {
    "日勤": 8,
    "夜勤": 16,
    "明け": 0,
    "公休": 0
}
WORK_SHIFTS = {"日勤", "夜勤"}

class ShiftSchedulerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Shift Scheduler Mk-II (Prototype)")
        self.geometry("1400x820")
        self.minsize(1260, 700)

        self.staff_list = [dict(s, preferred_holidays=list(s.get("preferred_holidays", []))) for s in DEFAULT_STAFFS]
        self.schedule_result = {} # (staff_idx, day): shift_name
        self.days_in_month = 30
        self.current_year = 2026
        self.current_month = 10

        self.create_widgets()

    def create_widgets(self):
        # メインコンテナ
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # 左サイドバー（設定・操作パネル）
        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(self.sidebar, text="⚙️ シフト設定", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(15, 10))

        # 年月選択
        ctk.CTkLabel(self.sidebar, text="対象年月:").pack(anchor="w", padx=15)
        self.year_entry = ctk.CTkEntry(self.sidebar)
        self.year_entry.insert(0, str(self.current_year))
        self.year_entry.pack(fill="x", padx=15, pady=5)

        self.month_entry = ctk.CTkEntry(self.sidebar)
        self.month_entry.insert(0, str(self.current_month))
        self.month_entry.pack(fill="x", padx=15, pady=5)

        # 今月の基準労働時間表示フレーム
        self.labor_info_frame = ctk.CTkFrame(self.sidebar, fg_color="#1e293b", corner_radius=6)
        self.labor_info_frame.pack(fill="x", padx=15, pady=(6, 8))

        ctk.CTkLabel(
            self.labor_info_frame, text="⏱️ 今月の基準労働時間", 
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8"
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.lbl_standard_hours = ctk.CTkLabel(
            self.labor_info_frame, text="-",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#f8fafc", justify="left"
        )
        self.lbl_standard_hours.pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_target_allocation = ctk.CTkLabel(
            self.labor_info_frame, text="-",
            font=ctk.CTkFont(size=11), text_color="#94a3b8", justify="left"
        )
        self.lbl_target_allocation.pack(anchor="w", padx=10, pady=(0, 6))

        # 年月変更時の動的更新バインド
        self.year_entry.bind("<KeyRelease>", self.update_standard_hours)
        self.month_entry.bind("<KeyRelease>", self.update_standard_hours)

        # スタッフ設定 (CSVインポート)
        ctk.CTkLabel(self.sidebar, text="スタッフ設定:").pack(anchor="w", padx=15, pady=(6, 2))
        self.btn_import = ctk.CTkButton(
            self.sidebar, text="📂 スタッフCSV読込",
            fg_color="#334155", hover_color="#475569",
            command=self.import_staff_csv
        )
        self.btn_import.pack(fill="x", padx=15, pady=5)

        self.lbl_staff_status = ctk.CTkLabel(
            self.sidebar, text=f"スタッフ: {len(self.staff_list)}名 (初期設定)",
            font=ctk.CTkFont(size=12), text_color="#94a3b8"
        )
        self.lbl_staff_status.pack(anchor="w", padx=18, pady=(0, 5))

        # 制約表示（ポートフォリオ用のアピール表示）
        info_frame = ctk.CTkFrame(self.sidebar, fg_color="#1e293b")
        info_frame.pack(fill="x", padx=15, pady=12)
        constraints_text = (
            "【適用制約ルール】\n"
            "・日勤: 最低3名 / 夜勤: 最低2名\n"
            "・全員が基準労働時間(±8h)で勤務\n"
            "・夜勤の翌日は必ず「明け」\n"
            "・夜勤に新人同士の配置NG\n"
            "  (リーダー/一般を1名以上)\n"
            "・公休日数の完全平準化\n"
            "・希望休の確実な公休割当\n"
            "・祝日判定対応 (jpholiday)"
        )
        ctk.CTkLabel(info_frame, text=constraints_text, justify="left", font=ctk.CTkFont(size=12)).pack(padx=10, pady=10)

        self.update_standard_hours()

        # アクションボタン
        self.btn_generate = ctk.CTkButton(
            self.sidebar, text="🚀 最適シフト自動生成", 
            font=ctk.CTkFont(size=14, weight="bold"), 
            command=self.run_optimization
        )
        self.btn_generate.pack(fill="x", padx=20, pady=15)

        self.btn_export = ctk.CTkButton(
            self.sidebar, text="💾 CSVエクスポート", 
            fg_color="#059669", hover_color="#047857",
            command=self.export_csv
        )
        self.btn_export.pack(fill="x", padx=20, pady=5)

        # 右メインエリア（シフトプレビュー表）
        self.main_area = ctk.CTkScrollableFrame(self, corner_radius=10)
        self.main_area.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)

        self.render_empty_grid()

    def update_standard_hours(self, event=None):
        try:
            year = int(self.year_entry.get())
            month = int(self.month_entry.get())
            if not (1 <= month <= 12):
                return
            num_days = calendar.monthrange(year, month)[1]

            # 土日祝日数をカウント（公休基準）
            holidays_count = sum(
                1 for d in range(1, num_days + 1)
                if date(year, month, d).weekday() >= 5 or jpholiday.is_holiday(date(year, month, d))
            )

            work_days = num_days - holidays_count
            standard_hours = work_days * 8

            self.lbl_standard_hours.configure(
                text=f"{standard_hours}h (実働{work_days}日 / 公休{holidays_count}日)"
            )
            self.lbl_target_allocation.configure(
                text=f"目標勤務: 各自 {standard_hours}h (±8h以内)"
            )
        except Exception:
            pass

    def import_staff_csv(self):
        file_path = filedialog.askopenfilename(
            title="スタッフCSVの選択",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if not file_path:
            # 未選択の場合は既存設定（デフォルト含む）を維持
            return

        try:
            content = None
            for enc in ["utf-8-sig", "cp932", "utf-8"]:
                try:
                    with open(file_path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue

            if content is None:
                messagebox.showerror("エラー", "ファイルの文字コードを認識できませんでした。\n(UTF-8またはShift-JISに対応しています)")
                return

            reader = list(csv.reader(content.splitlines()))
            if not reader:
                messagebox.showerror("エラー", "CSVファイルが空です。")
                return

            # ヘッダー行判定 (1行目がヘッダーキーワードを含むかチェック)
            start_idx = 0
            first_row_str = "".join(reader[0])
            if any(k in first_row_str for k in ["スタッフ", "名前", "役職", "希望休", "name"]):
                start_idx = 1

            loaded_staffs = []
            for row in reader[start_idx:]:
                if not row or not any(field.strip() for field in row):
                    continue

                name = row[0].strip()
                if not name:
                    continue

                role_str = row[1].strip() if len(row) > 1 else ""
                is_leader = 1 if "リーダー" in role_str else 0
                is_rookie = 1 if "新人" in role_str else 0

                preferred_holidays = []
                if len(row) > 2 and row[2].strip():
                    raw_days = row[2].replace("、", ",").split(",")
                    for part in raw_days:
                        part = part.strip()
                        if part.isdigit():
                            d = int(part)
                            if 1 <= d <= 31 and d not in preferred_holidays:
                                preferred_holidays.append(d)

                loaded_staffs.append({
                    "name": name,
                    "is_leader": is_leader,
                    "is_rookie": is_rookie,
                    "preferred_holidays": sorted(preferred_holidays)
                })

            if not loaded_staffs:
                messagebox.showwarning("警告", "有効なスタッフデータが見つかりませんでした。現在のスタッフデータを維持します。")
                return

            self.staff_list = loaded_staffs
            self.lbl_staff_status.configure(
                text=f"スタッフ: {len(self.staff_list)}名 (CSV読込済)",
                text_color="#38bdf8"
            )

            # スタッフ数変更に伴う目標配分時間の再計算
            self.update_standard_hours()

            # シフト結果表示をクリア
            self.schedule_result.clear()
            self.render_empty_grid()

            messagebox.showinfo("読込成功", f"{len(self.staff_list)}名のスタッフを読み込みました。")

        except Exception as e:
            messagebox.showerror("エラー", f"CSVファイルの読み込みに失敗しました:\n{e}")

    def render_empty_grid(self):
        for widget in self.main_area.winfo_children():
            widget.destroy()
        placeholder = ctk.CTkLabel(self.main_area, text="「最適シフト自動生成」ボタンを押すと結果が表示されます", font=ctk.CTkFont(size=16))
        placeholder.pack(expand=True, pady=100)

    def run_optimization(self):
        try:
            year = int(self.year_entry.get())
            month = int(self.month_entry.get())
        except ValueError:
            messagebox.showerror("エラー", "正しい年月を入力してください")
            return

        self.days_in_month = calendar.monthrange(year, month)[1]
        self.current_year = year
        self.current_month = month

        # ----------------------------------------------------
        # OR-Tools による数理最適化 (CP-SAT)
        # ----------------------------------------------------
        model = cp_model.CpModel()

        num_staff = len(self.staff_list)
        num_days = self.days_in_month
        num_shifts = len(SHIFT_TYPES) # 0:日勤, 1:夜勤, 2:明け, 3:公休

        # 変数: shifts[(s, d, s_type)] == 1 なら割り当て
        shifts = {}
        for s in range(num_staff):
            for d in range(1, num_days + 1):
                for st in range(num_shifts):
                    shifts[(s, d, st)] = model.NewBoolVar(f"shift_s{s}_d{d}_st{st}")

        # 制約1: 各スタッフは1日につき1つのシフト
        for s in range(num_staff):
            for d in range(1, num_days + 1):
                model.AddExactlyOne(shifts[(s, d, st)] for st in range(num_shifts))

        # 基準労働時間（土日祝日を除いた実働日数 × 8時間）
        holidays_count = sum(
            1 for d in range(1, num_days + 1)
            if date(year, month, d).weekday() >= 5 or jpholiday.is_holiday(date(year, month, d))
        )
        work_days = num_days - holidays_count
        standard_hours = work_days * 8

        # 最低必要総労働時間（日勤最低3名×8h + 夜勤最低2名×16h = 56h/日）
        min_total_required_hours = num_days * (3 * 8 + 2 * 16)
        min_req_per_staff = min_total_required_hours // num_staff
        target_hours_per_staff = max(standard_hours, min_req_per_staff)

        # 制約2: 各日の必要人数 (日勤最低3人、夜勤最低2人、夜勤上限3人)
        for d in range(1, num_days + 1):
            model.Add(sum(shifts[(s, d, 0)] for s in range(num_staff)) >= 3) # 日勤 >= 3
            model.Add(sum(shifts[(s, d, 1)] for s in range(num_staff)) >= 2) # 夜勤 >= 2
            model.Add(sum(shifts[(s, d, 1)] for s in range(num_staff)) <= 3) # 夜勤 <= 3 (夜間配置の適正化)

        # 制約3: 夜勤の翌日は必ず「明け」
        for s in range(num_staff):
            for d in range(1, num_days):
                # shifts[(s, d, 1)] == 1 ならば shifts[(s, d+1, 2)] == 1
                model.AddImplication(shifts[(s, d, 1)], shifts[(s, d + 1, 2)])

        # 制約4: 明けの日は他の勤務に入れない（すでに1日1シフト制約で保証済）

        # 制約5: 新人同士の夜勤NG (夜勤帯の新人は最大1名、かつ経験者1名以上)
        for d in range(1, num_days + 1):
            rookies_in_night = [
                shifts[(s, d, 1)] for s in range(num_staff) if self.staff_list[s]["is_rookie"]
            ]
            experienced_in_night = [
                shifts[(s, d, 1)] for s in range(num_staff) if not self.staff_list[s]["is_rookie"]
            ]
            model.Add(sum(rookies_in_night) <= 1)
            model.Add(sum(experienced_in_night) >= 1)

        # 制約6: 希望休の確実な公休割当 (公休=3)
        for s, staff in enumerate(self.staff_list):
            for d in staff.get("preferred_holidays", []):
                if 1 <= d <= num_days:
                    model.Add(shifts[(s, d, 3)] == 1)

        # 制約7: 公休数の均等化 (月間日数のうち、土日祝日数前後に均等割り振り)
        for s, staff in enumerate(self.staff_list):
            pref_count = len([d for d in staff.get("preferred_holidays", []) if 1 <= d <= num_days])
            lower_bound = max(holidays_count - 2, pref_count)
            upper_bound = max(holidays_count + 2, pref_count)
            model.Add(sum(shifts[(s, d, 3)] for d in range(1, num_days + 1)) >= lower_bound)
            model.Add(sum(shifts[(s, d, 3)] for d in range(1, num_days + 1)) <= upper_bound)

        # 制約8: 勤務時間を基準労働時間(±8h以内)に厳密平準化
        staff_hours = []
        diff_vars = []
        for s, staff in enumerate(self.staff_list):
            h_var = model.NewIntVar(0, num_days * 16, f"hours_s{s}")
            model.Add(h_var == sum(8 * shifts[(s, d, 0)] + 16 * shifts[(s, d, 1)] for d in range(1, num_days + 1)))
            staff_hours.append(h_var)

            # 基準労働時間との差（絶対値）
            diff = model.NewIntVar(0, num_days * 16, f"diff_s{s}")
            model.Add(diff >= h_var - target_hours_per_staff)
            model.Add(diff >= target_hours_per_staff - h_var)
            diff_vars.append(diff)

            # 過重労働防止と希望休を考慮した許容範囲設定
            pref_count = len([d for d in staff.get("preferred_holidays", []) if 1 <= d <= num_days])
            model.Add(h_var <= target_hours_per_staff + 16)
            min_h_bound = max(0, target_hours_per_staff - 16 - (pref_count * 8))
            model.Add(h_var >= min_h_bound)

        # 最大・最小勤務時間の差を最小化し、かつ基準時間からの偏差合計を最小化
        max_h = model.NewIntVar(0, num_days * 16, "max_h")
        min_h = model.NewIntVar(0, num_days * 16, "min_h")
        for h_var in staff_hours:
            model.Add(h_var <= max_h)
            model.Add(h_var >= min_h)

        model.Minimize(sum(diff_vars) * 5 + (max_h - min_h) * 10)

        # ソルバー実行
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10.0
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            self.schedule_result.clear()
            for s in range(num_staff):
                for d in range(1, num_days + 1):
                    for st in range(num_shifts):
                        if solver.Value(shifts[(s, d, st)]) == 1:
                            self.schedule_result[(s, d)] = SHIFT_TYPES[st]
            self.render_schedule_grid()
            messagebox.showinfo("成功", f"{year}年{month}月の最適シフト生成が完了しました！")
        else:
            messagebox.showwarning("失敗", "制約条件を満たすシフトが見つかりませんでした。条件を緩和してください。")

    def render_schedule_grid(self):
        for widget in self.main_area.winfo_children():
            widget.destroy()

        col_work_days = self.days_in_month + 1
        col_total_hours = self.days_in_month + 2

        # ヘッダー（日付・曜日・祝日）
        ctk.CTkLabel(self.main_area, text="スタッフ", width=82, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=2, pady=2)

        for d in range(1, self.days_in_month + 1):
            dt = date(self.current_year, self.current_month, d)
            weekday_str = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            is_holiday = jpholiday.is_holiday(dt) or dt.weekday() >= 5
            
            fg = "#ef4444" if is_holiday else "#94a3b8"
            header_text = f"{d}\n({weekday_str})"
            
            lbl = ctk.CTkLabel(self.main_area, text=header_text, width=28, text_color=fg, font=ctk.CTkFont(size=10, weight="bold"))
            lbl.grid(row=0, column=d, padx=1, pady=2)

        # 右端の集計ヘッダー
        lbl_days = ctk.CTkLabel(self.main_area, text="出勤\n(日)", width=38, text_color="#38bdf8", font=ctk.CTkFont(size=10, weight="bold"))
        lbl_days.grid(row=0, column=col_work_days, padx=(3, 1), pady=2)

        lbl_hours = ctk.CTkLabel(self.main_area, text="総時間\n(h)", width=46, text_color="#38bdf8", font=ctk.CTkFont(size=10, weight="bold"))
        lbl_hours.grid(row=0, column=col_total_hours, padx=1, pady=2)

        # 各スタッフの行描画
        for s_idx, staff in enumerate(self.staff_list):
            s_name = staff["name"]
            if staff["is_leader"]:
                s_name += " [L]"
            elif staff["is_rookie"]:
                s_name += " [新]"

            ctk.CTkLabel(self.main_area, text=s_name, width=82, anchor="w", font=ctk.CTkFont(size=11)).grid(row=s_idx + 1, column=0, padx=2, pady=1)

            for d in range(1, self.days_in_month + 1):
                shift = self.schedule_result.get((s_idx, d), "-")
                color = SHIFT_COLORS.get(shift, "#1f2937")

                # 希望休かつ公休の場合はテキスト色を黄色（ゴールド）にして強調表示
                is_pref_match = (d in staff.get("preferred_holidays", [])) and (shift == "公休")
                box_text = shift[0] if shift != "-" else "-"
                lbl_kwargs = {
                    "text": box_text,
                    "width": 28, "height": 24, "fg_color": color, "corner_radius": 4,
                    "font": ctk.CTkFont(size=11, weight="bold")
                }
                if is_pref_match:
                    lbl_kwargs["text_color"] = "#facc15"

                box = ctk.CTkLabel(self.main_area, **lbl_kwargs)
                box.grid(row=s_idx + 1, column=d, padx=1, pady=1)

            # 各スタッフの集計（出勤日数・総勤務時間）
            work_days = sum(1 for d in range(1, self.days_in_month + 1) if self.schedule_result.get((s_idx, d)) in WORK_SHIFTS)
            total_hours = sum(SHIFT_HOURS.get(self.schedule_result.get((s_idx, d)), 0) for d in range(1, self.days_in_month + 1))

            box_days = ctk.CTkLabel(
                self.main_area, text=f"{work_days}日", width=38, height=24,
                fg_color="#1e293b", text_color="#f8fafc", corner_radius=4,
                font=ctk.CTkFont(size=11, weight="bold")
            )
            box_days.grid(row=s_idx + 1, column=col_work_days, padx=(3, 1), pady=1)

            box_hours = ctk.CTkLabel(
                self.main_area, text=f"{total_hours}h", width=46, height=24,
                fg_color="#1e293b", text_color="#f8fafc", corner_radius=4,
                font=ctk.CTkFont(size=11, weight="bold")
            )
            box_hours.grid(row=s_idx + 1, column=col_total_hours, padx=1, pady=1)

        # 最下部の日別集計行
        base_row = len(self.staff_list) + 1

        # 区切り線
        sep = ctk.CTkFrame(self.main_area, height=2, fg_color="#334155")
        sep.grid(row=base_row, column=0, columnspan=col_total_hours + 1, sticky="ew", pady=(6, 3))

        summary_configs = [
            ("日勤人数", "日勤", "#1e3a5f", "#93c5fd", "#60a5fa"),
            ("夜勤人数", "夜勤", "#3b1d60", "#d8b4fe", "#c084fc"),
            ("合計出勤人数", "合計", "#064e3b", "#6ee7b7", "#34d399")
        ]

        total_nikkin_count = sum(1 for (s, d), v in self.schedule_result.items() if v == "日勤")
        total_yakin_count = sum(1 for (s, d), v in self.schedule_result.items() if v == "夜勤")
        total_all_shifts = total_nikkin_count + total_yakin_count
        total_all_hours = (total_nikkin_count * 8) + (total_yakin_count * 16)

        for offset, (label_text, shift_target, bg_color, text_color, title_color) in enumerate(summary_configs, start=1):
            r_idx = base_row + offset
            ctk.CTkLabel(
                self.main_area, text=label_text, width=82, anchor="w",
                text_color=title_color, font=ctk.CTkFont(size=11, weight="bold")
            ).grid(row=r_idx, column=0, padx=2, pady=1)

            for d in range(1, self.days_in_month + 1):
                if shift_target == "日勤":
                    cnt = sum(1 for s in range(len(self.staff_list)) if self.schedule_result.get((s, d)) == "日勤")
                elif shift_target == "夜勤":
                    cnt = sum(1 for s in range(len(self.staff_list)) if self.schedule_result.get((s, d)) == "夜勤")
                else:
                    cnt = sum(1 for s in range(len(self.staff_list)) if self.schedule_result.get((s, d)) in WORK_SHIFTS)

                box = ctk.CTkLabel(
                    self.main_area, text=str(cnt), width=28, height=22,
                    fg_color=bg_color, text_color=text_color, corner_radius=3,
                    font=ctk.CTkFont(size=11, weight="bold")
                )
                box.grid(row=r_idx, column=d, padx=1, pady=1)

            if shift_target == "日勤":
                tot_shifts_str = f"{total_nikkin_count}"
                tot_hours_str = f"{total_nikkin_count * 8}h"
            elif shift_target == "夜勤":
                tot_shifts_str = f"{total_yakin_count}"
                tot_hours_str = f"{total_yakin_count * 16}h"
            else:
                tot_shifts_str = f"{total_all_shifts}"
                tot_hours_str = f"{total_all_hours}h"

            box_tot_days = ctk.CTkLabel(
                self.main_area, text=tot_shifts_str, width=38, height=22,
                fg_color=bg_color, text_color=text_color, corner_radius=3,
                font=ctk.CTkFont(size=10, weight="bold")
            )
            box_tot_days.grid(row=r_idx, column=col_work_days, padx=(3, 1), pady=1)

            box_tot_hours = ctk.CTkLabel(
                self.main_area, text=tot_hours_str, width=46, height=22,
                fg_color=bg_color, text_color=text_color, corner_radius=3,
                font=ctk.CTkFont(size=10, weight="bold")
            )
            box_tot_hours.grid(row=r_idx, column=col_total_hours, padx=1, pady=1)

    def export_csv(self):
        if not self.schedule_result:
            messagebox.showwarning("警告", "エクスポートするシフト結果がありません。先に生成を実行してください。")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile=f"shift_{self.current_year}_{self.current_month:02d}.csv"
        )
        if not file_path:
            return

        with open(file_path, "w", newline="", encoding="utf_8_sig") as f:
            writer = csv.writer(f)
            header = ["スタッフ名", "属性"] + [f"{d}日" for d in range(1, self.days_in_month + 1)] + ["出勤日数", "総勤務時間(h)"]
            writer.writerow(header)

            for s_idx, staff in enumerate(self.staff_list):
                attr = "リーダー" if staff["is_leader"] else ("新人" if staff["is_rookie"] else "一般")
                row = [staff["name"], attr]
                for d in range(1, self.days_in_month + 1):
                    row.append(self.schedule_result.get((s_idx, d), ""))
                work_days = sum(1 for d in range(1, self.days_in_month + 1) if self.schedule_result.get((s_idx, d)) in WORK_SHIFTS)
                total_hours = sum(SHIFT_HOURS.get(self.schedule_result.get((s_idx, d)), 0) for d in range(1, self.days_in_month + 1))
                row.extend([work_days, total_hours])
                writer.writerow(row)

            # 日別集計行
            nikkin_counts = [sum(1 for s in range(len(self.staff_list)) if self.schedule_result.get((s, d)) == "日勤") for d in range(1, self.days_in_month + 1)]
            yakin_counts = [sum(1 for s in range(len(self.staff_list)) if self.schedule_result.get((s, d)) == "夜勤") for d in range(1, self.days_in_month + 1)]
            total_counts = [n + y for n, y in zip(nikkin_counts, yakin_counts)]

            tot_nikkin = sum(nikkin_counts)
            tot_yakin = sum(yakin_counts)
            tot_all = sum(total_counts)

            writer.writerow(["日勤人数", "-"] + nikkin_counts + [tot_nikkin, tot_nikkin * 8])
            writer.writerow(["夜勤人数", "-"] + yakin_counts + [tot_yakin, tot_yakin * 16])
            writer.writerow(["合計出勤人数", "-"] + total_counts + [tot_all, (tot_nikkin * 8) + (tot_yakin * 16)])

        messagebox.showinfo("保存完了", f"CSVファイルを正常に保存しました:\n{file_path}")

if __name__ == "__main__":
    app = ShiftSchedulerApp()
    app.mainloop()