import streamlit as st
import pandas as pd
import hashlib
from datetime import datetime
import io
import os
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

# ──────────────────────────────────────────────
# ページ設定
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="在庫増減チェッカー",
    page_icon="📦",
    layout="wide",
)

# ──────────────────────────────────────────────
# 認証設定（Streamlit Secrets から読み込み）
# ──────────────────────────────────────────────
# パスワードのハッシュ値を .streamlit/secrets.toml (ローカル)
# または Streamlit Cloud の Secrets 設定に記載してください。
#

_USERS: dict[str, str] = {}
if "passwords" in st.secrets:
    for uid, hashed_pw in st.secrets["passwords"].items():
        _USERS[uid] = hashed_pw


def _verify(user_id: str, password: str) -> bool:
    """ID とパスワードを検証する。"""
    if user_id not in _USERS:
        return False
    return _USERS[user_id] == hashlib.sha256(password.encode()).hexdigest()


# ──────────────────────────────────────────────
# ログイン画面
# ──────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.current_user = ""

if not st.session_state.authenticated:
    # ログインフォームを中央寄せ
    _lcol, _ccol, _rcol = st.columns([1, 2, 1])
    with _ccol:
        st.markdown(
            '<div style="text-align:center;margin-top:3rem;">'
            '<h1 style="font-size:3rem;margin-bottom:0;">📦</h1>'
            '<h2 style="margin-top:0.2rem;">在庫増減チェッカー</h2>'
            '<p style="color:#6b7280;">ログインしてください</p>'
            "</div>",
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            uid = st.text_input("ユーザー ID", placeholder="ID を入力")
            pwd = st.text_input("パスワード", type="password", placeholder="パスワードを入力")
            submitted = st.form_submit_button("ログイン", use_container_width=True)

            if submitted:
                if _verify(uid, pwd):
                    st.session_state.authenticated = True
                    st.session_state.current_user = uid
                    st.rerun()
                else:
                    st.error("⚠️ ユーザー ID またはパスワードが正しくありません。")

    st.stop()  # ログインするまでここで停止

# ──────────────────────────────────────────────
# 列名の自動検出ヘルパー
# ──────────────────────────────────────────────
_JAN_CANDIDATES = ["JANコード", "JAN", "janコード", "jan_code", "barcode", "バーコード", "商品コード"]
_PRODUCT_CANDIDATES = ["商品名", "品名", "製品名", "品番", "商品", "アイテム名", "item", "product"]
_QTY_CANDIDATES = ["個数", "数量", "在庫数", "在庫", "stock", "quantity", "qty"]
_SHELF_CANDIDATES = ["棚番", "棚", "shelf", "ロケーション", "location", "配置", "売り場"]


def _find_column(columns: pd.Index, candidates: list[str]) -> str | None:
    """大文字小文字を無視して候補名に一致する列を返す。"""
    lower_map = {c.strip().lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _find_master_sheet(sheet_names: list[str]) -> str | None:
    """シート名に 'マスター' を含むシートを自動検出する。"""
    for name in sheet_names:
        if "マスター" in name or "マスタ" in name or "master" in name.lower():
            return name
    return None


def _extract_and_merge(
    xls: pd.ExcelFile,
    main_sheet: str,
    master_sheet: str,
) -> tuple[pd.DataFrame | None, str | None]:
    """
    メインシートとマスターシートからデータを抽出し、
    JANコードをキーに結合して「商品名」「個数」の対応表を返す。

    戻り値: (DataFrame or None, error_message or None)
    """
    # ── メインシート読み込み ──
    try:
        df_main = pd.read_excel(xls, sheet_name=main_sheet, engine="openpyxl")
    except Exception as e:
        return None, f"メインシート「{main_sheet}」の読み込みに失敗: {e}"

    jan_col_main = _find_column(df_main.columns, _JAN_CANDIDATES)
    qty_col = _find_column(df_main.columns, _QTY_CANDIDATES)

    if jan_col_main is None:
        return None, (
            f"メインシート「{main_sheet}」に JANコード列が見つかりません。\n"
            f"  検出対象: {', '.join(_JAN_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_main.columns.tolist())}"
        )
    if qty_col is None:
        return None, (
            f"メインシート「{main_sheet}」に 個数列が見つかりません。\n"
            f"  検出対象: {', '.join(_QTY_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_main.columns.tolist())}"
        )

    shelf_col_main = _find_column(df_main.columns, _SHELF_CANDIDATES)
    if shelf_col_main:
        main_data = df_main[[jan_col_main, qty_col, shelf_col_main]].copy()
        main_data.columns = ["JANコード", "個数", "棚番"]
    else:
        main_data = df_main[[jan_col_main, qty_col]].copy()
        main_data.columns = ["JANコード", "個数"]

    main_data["個数"] = pd.to_numeric(main_data["個数"], errors="coerce").fillna(0).astype(int)
    main_data = main_data.dropna(subset=["JANコード"])

    # ── マスターシート読み込み ──
    try:
        df_master = pd.read_excel(xls, sheet_name=master_sheet, engine="openpyxl")
    except Exception as e:
        return None, f"マスターシート「{master_sheet}」の読み込みに失敗: {e}"

    jan_col_master = _find_column(df_master.columns, _JAN_CANDIDATES)
    product_col = _find_column(df_master.columns, _PRODUCT_CANDIDATES)

    if jan_col_master is None:
        return None, (
            f"マスターシート「{master_sheet}」に JANコード列が見つかりません。\n"
            f"  検出対象: {', '.join(_JAN_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_master.columns.tolist())}"
        )
    if product_col is None:
        return None, (
            f"マスターシート「{master_sheet}」に 商品名列が見つかりません。\n"
            f"  検出対象: {', '.join(_PRODUCT_CANDIDATES)}\n"
            f"  実際の列名: {', '.join(df_master.columns.tolist())}"
        )

    shelf_col_master = _find_column(df_master.columns, _SHELF_CANDIDATES)
    if shelf_col_master:
        master_data = df_master[[jan_col_master, product_col, shelf_col_master]].copy()
        master_data.columns = ["JANコード", "商品名", "棚番"]
    else:
        master_data = df_master[[jan_col_master, product_col]].copy()
        master_data.columns = ["JANコード", "商品名"]
        
    master_data = master_data.dropna(subset=["JANコード", "商品名"])
    # マスターの重複を除去（最初の出現を採用）
    master_data = master_data.drop_duplicates(subset=["JANコード"], keep="first")

    # ── JAN コードをキーに結合 ──
    # VLOOKUP等の数式は無視し、マスターの実データで結合
    merged = pd.merge(main_data, master_data, on="JANコード", how="left")

    # 棚番の解決（mainとmasterの両方にある場合はmain優先）
    if "棚番_x" in merged.columns and "棚番_y" in merged.columns:
        merged["棚番"] = merged["棚番_x"].combine_first(merged["棚番_y"])
        merged = merged.drop(columns=["棚番_x", "棚番_y"])
    elif "棚番_x" in merged.columns:
        merged = merged.rename(columns={"棚番_x": "棚番"})
    elif "棚番_y" in merged.columns:
        merged = merged.rename(columns={"棚番_y": "棚番"})
    else:
        if "棚番" not in merged.columns:
            merged["棚番"] = "-"
    
    merged["棚番"] = merged["棚番"].fillna("-").astype(str)

    # マスターに存在しない JAN コードは商品名を「（不明）」で埋める
    merged["商品名"] = merged["商品名"].fillna("（不明：マスター未登録）")

    # 商品名ごとに個数と棚番をまとめる
    result = merged.groupby("商品名", as_index=False).agg({
        "個数": "sum",
        "棚番": "first"
    })

    return result, None

def _create_pdf(df: pd.DataFrame) -> bytes:
    """データフレームからPDFを生成しバイト列で返す"""
    font_path = "ipaexg.ttf"
    font_name = "IPAexGothic"
    
    # 1. 日本語フォント(IPAexGothic)の登録
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont(font_name, font_path))
    else:
        font_name = "Helvetica"

    # PDF用データフレームのコピーを作成し、列名をシンプルに変更する
    pdf_df = df.copy()
    
    # 商品名が長すぎる場合に切り詰める（棚番への侵食回避）
    if "商品名" in pdf_df.columns:
        pdf_df["商品名"] = pdf_df["商品名"].astype(str).apply(lambda x: x[:22] + "..." if len(x) > 22 else x)
        
    pdf_cols = list(pdf_df.columns)
    
    has_rate = "減少率(%)" in pdf_cols
    
    rename_mapping = {}
    file_cols_list = [c for c in pdf_cols if c not in ("商品名", "棚番", "増減数", "減少率(%)")]
    
    for c in pdf_cols:
        if c in ("商品名", "棚番", "増減数", "減少率(%)"):
            rename_mapping[c] = c
        else:
            try:
                idx = file_cols_list.index(c)
                if len(file_cols_list) == 1:
                    rename_mapping[c] = "在庫"
                elif idx == 0:
                    rename_mapping[c] = "旧在庫"
                elif idx == len(file_cols_list) - 1:
                    rename_mapping[c] = "新在庫"
                else:
                    rename_mapping[c] = f"中間在庫{idx}"
            except ValueError:
                rename_mapping[c] = c
                
    pdf_df.rename(columns=rename_mapping, inplace=True)

    buffer = io.BytesIO()
    
    # A4横向きで余白を設定
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=landscape(A4),
        rightMargin=30, 
        leftMargin=30, 
        topMargin=30, 
        bottomMargin=30
    )

    # データを2次元リストに変換 (列名 + データ行)
    data = [pdf_df.columns.tolist()] + pdf_df.values.tolist()

    usable_width = 782
    num_cols = len(pdf_df.columns)
    
    if num_cols > 1:
        col_widths = []
        first_col_w = 260
        remaining_width = usable_width - first_col_w
        remaining_cols = num_cols - 1
        
        has_shelf = "棚番" in pdf_df.columns
        shelf_col_w = 60
        if has_shelf:
            remaining_width -= shelf_col_w
            remaining_cols -= 1
            
        other_col_w = remaining_width / max(1, remaining_cols)
        
        for c in pdf_df.columns:
            if c == "商品名":
                col_widths.append(first_col_w)
            elif c == "棚番":
                col_widths.append(shelf_col_w)
            else:
                col_widths.append(other_col_w)
    else:
        col_widths = [usable_width]

    table = Table(data, colWidths=col_widths)

    # テーブルのスタイル設定
    style = TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),            # 全体に日本語フォントを適用
        ('FONTSIZE', (0, 0), (-1, -1), 10),                   # フォントサイズ
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),    # ヘッダー背景色 (グレー)
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),         # ヘッダー文字色 (黒)
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),                 # ヘッダーはすべて中央揃え
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),                   # 商品名のみ左揃え
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),                 # 以降の数値列は右揃え
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),               # 垂直中央
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),                # ヘッダーの下部余白
        ('TOPPADDING', (0, 0), (-1, 0), 8),                   # ヘッダーの上部余白
        ('GRID', (0, 0), (-1, -1), 1, colors.black),          # 全体に1ptの黒罫線
    ])
    
    # データ行に対し、背景色の縞模様と文字色(条件付き書式)を適用
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor("#f8fafc"))
        
        # 棚番が倉庫の場合の色分け
        try:
            shelf_idx = pdf_df.columns.tolist().index("棚番")
            if str(data[i][shelf_idx]) == "倉庫":
                style.add('TEXTCOLOR', (shelf_idx, i), (shelf_idx, i), colors.HexColor("#d35400"))
                # 背景色をつけると縞模様と競合する可能性があるので上書き
                style.add('BACKGROUND', (shelf_idx, i), (shelf_idx, i), colors.HexColor("#fdebd0"))
        except (ValueError, IndexError):
            pass

        # 増減数列のインデックスを探して色分け
        try:
            diff_idx = pdf_df.columns.tolist().index("増減数")
            diff_num = int(data[i][diff_idx])
        except (ValueError, TypeError, IndexError):
            diff_idx = None
            diff_num = 0
            
        if diff_idx is not None:
            if diff_num < 0:
                style.add('TEXTCOLOR', (diff_idx, i), (diff_idx, i), colors.red)
            elif diff_num > 0:
                style.add('TEXTCOLOR', (diff_idx, i), (diff_idx, i), colors.green)

    table.setStyle(style)
    
    # PDF構築
    doc.build([table])
    return buffer.getvalue()


# ──────────────────────────────────────────────
# セッション初期化
# ──────────────────────────────────────────────
if "slot_ids" not in st.session_state:
    # 各スロットに一意の ID を割り振る（削除対応のため連番ではなくカウンター管理）
    st.session_state.slot_ids = [0, 1]
    st.session_state.next_slot_id = 2

if "history" not in st.session_state:
    st.session_state.history = []  # list[dict]  比較履歴

# ──────────────────────────────────────────────
# スロット操作の コールバック
# ──────────────────────────────────────────────
def _add_slot():
    st.session_state.slot_ids.append(st.session_state.next_slot_id)
    st.session_state.next_slot_id += 1


def _remove_slot(slot_id: int):
    """指定 ID のスロットを削除する。最低 2 枠は維持。"""
    if len(st.session_state.slot_ids) <= 2:
        return
    st.session_state.slot_ids = [s for s in st.session_state.slot_ids if s != slot_id]


# ──────────────────────────────────────────────
# サイドバー：ファイルアップロード
# ──────────────────────────────────────────────
with st.sidebar:
    st.header("📂 ファイルアップロード")
    st.caption("上から順に **古い → 新しい** の順でアップロードしてください。")
    st.caption("💡 各ファイルは「メインシート（JANコード＋個数）」と"
               "「マスターシート（JANコード＋商品名）」を含む Excel ブックを想定しています。")

    slot_ids = st.session_state.slot_ids
    total_slots = len(slot_ids)

    uploaded_files: list[tuple[str | None, pd.DataFrame | None, str | None]] = []

    for pos, sid in enumerate(slot_ids):
        label = f"📄 {pos + 1} 番目のファイル"
        if pos == 0:
            label += "（最古）"
        elif pos == total_slots - 1:
            label += "（最新）"

        file = st.file_uploader(
            label,
            type=["xlsx", "xlsm"],
            key=f"file_{sid}",
        )

        # 削除ボタン（3 枠以上あるとき表示）
        if total_slots > 2:
            if st.button(f"✕ {pos + 1} 番目の枠を削除", key=f"del_{sid}"):
                _remove_slot(sid)
                st.rerun()

        if file is not None:
            try:
                xls = pd.ExcelFile(file, engine="openpyxl")
                sheet_names = xls.sheet_names

                # ── メインシート選択（デフォルト: 1 番目のシート）──
                main_sheet_default = 0
                main_sheet = st.selectbox(
                    f"📋 メインシート（{pos + 1} 番目）",
                    options=sheet_names,
                    index=main_sheet_default,
                    key=f"main_sheet_{sid}",
                    help="JANコードと個数が記載されたシートを選択してください",
                )

                # ── マスターシート選択（自動検出 or ユーザー指定）──
                auto_master = _find_master_sheet(sheet_names)
                if auto_master:
                    master_default_idx = sheet_names.index(auto_master)
                else:
                    # メインシートでない最初のシートを候補にする
                    master_default_idx = 1 if len(sheet_names) > 1 else 0

                master_sheet = st.selectbox(
                    f"📑 マスターシート（{pos + 1} 番目）",
                    options=sheet_names,
                    index=master_default_idx,
                    key=f"master_sheet_{sid}",
                    help="JANコードと商品名が記載されたマスターデータのシートを選択してください",
                )

                # ── データ抽出＆結合 ──
                extracted, err = _extract_and_merge(xls, main_sheet, master_sheet)
                if err:
                    uploaded_files.append((file.name, None, err))
                else:
                    uploaded_files.append((file.name, extracted, None))

            except Exception as e:
                uploaded_files.append((file.name, None, str(e)))
        else:
            uploaded_files.append((None, None, None))

    # 追加ボタン
    if st.button("＋ 新しい比較ファイルを追加", use_container_width=True):
        _add_slot()
        st.rerun()

    st.divider()
    st.markdown(f"**現在の枠数:** {total_slots}")

    # ログアウト
    st.divider()
    st.markdown(f"👤 ログイン中: **{st.session_state.current_user}**")
    if st.button("🚪 ログアウト", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.current_user = ""
        st.rerun()

# ──────────────────────────────────────────────
# メインエリア
# ──────────────────────────────────────────────

# アップロード済みファイル一覧
valid_frames: list[tuple[int, str, pd.DataFrame]] = []

for idx, (name, df, err) in enumerate(uploaded_files):
    if name is None:
        continue
    if err is not None:
        st.warning(f"⚠️ {idx + 1} 番目のファイル（{name}）の読み込みに失敗しました:\n{err}")
        continue

    # df は既に _extract_and_merge で「商品名」「個数」に整形済み
    valid_frames.append((idx + 1, name, df))

# ──────────────────────────────────────────────
# データ結合 & 表示
# ──────────────────────────────────────────────
if len(valid_frames) < 2:
    st.info("📌 比較するには **2 つ以上** の Excel ファイルをアップロードしてください。")

    # 履歴だけは表示
    if st.session_state.history:
        st.divider()
        st.subheader("🕘 比較履歴")
        for hi, h in enumerate(reversed(st.session_state.history)):
            with st.expander(f"📋 {h['timestamp']}　（{h['file_count']} ファイル / {h['product_count']} 商品）", expanded=False):
                st.dataframe(h["dataframe"], use_container_width=True)
    st.stop()

# 結合と棚番の収集
merged = valid_frames[0][2][["商品名"]].copy()
shelf_mapping = {}
col_labels: list[str] = []

for file_no, fname, frame in valid_frames:
    col_name = f"{file_no}番目({fname})"
    col_labels.append(col_name)
    merged = merged.merge(
        frame[["商品名", "個数"]].rename(columns={"個数": col_name}),
        on="商品名",
        how="outer",
    )
    
    for _, row in frame.iterrows():
        if "棚番" in row and pd.notna(row["棚番"]) and row["棚番"] != "-" and str(row["棚番"]).lower() != "nan":
            shelf_mapping[row["商品名"]] = row["棚番"]

# 棚番列を商品名の次に追加
def _get_shelf_label(product_name):
    val = str(shelf_mapping.get(product_name, "-")).strip()
    if val.endswith(".0"):
        val = val[:-2]
    # 数値（数字）が含まれていない場合は「倉庫」として表記する
    if not any(char.isdigit() for char in val):
        return "倉庫"
    return val

merged.insert(1, "棚番", merged["商品名"].map(_get_shelf_label))

# NaN を 0 にする前に、新商品（過去データが null/未定義）かどうかのフラグを作成
oldest_col = col_labels[0]
newest_col = col_labels[-1]
merged["_is_new"] = merged[oldest_col].isna()

# NaN を 0 に
for c in col_labels:
    merged[c] = merged[c].fillna(0).astype(int)

# 増減列
merged["増減数"] = merged[newest_col] - merged[oldest_col]

def _calc_decrease_rate(row):
    prev = row[oldest_col]
    curr = row[newest_col]
    if prev <= 10:
        return "-"
    try:
        rate = ((prev - curr) / prev) * 100
        return f"{rate:.1f}%"
    except ZeroDivisionError:
        return "-"

merged["減少率(%)"] = merged.apply(_calc_decrease_rate, axis=1)

def _calc_decrease_val(row):
    prev = row[oldest_col]
    curr = row[newest_col]
    if prev <= 0:
        return float('nan')
    return ((prev - curr) / prev) * 100.0

merged["_decrease_rate_val"] = merged.apply(_calc_decrease_val, axis=1)

# ソート
merged = merged.sort_values(["棚番", "商品名"]).reset_index(drop=True)

# 色付け関数
def _style_diff(val):
    if val > 0:
        return "color: #27ae60; font-weight: 700"
    elif val < 0:
        return "color: #e74c3c; font-weight: 700"
    return "color: #95a5a6"

def _highlight_warehouse_row(row):
    # 棚番が「倉庫」の場合、行全体にスタイルを適用する
    if "棚番" in row.index and str(row["棚番"]).strip() == "倉庫":
        return ["color: #d35400; background-color: #fdebd0; font-weight: 700"] * len(row)
    return [""] * len(row)

# ──────────────────────────────────────────────
# ダッシュボード（重要指標）
# ──────────────────────────────────────────────
st.markdown("### 📈 在庫サマリー")
with st.container(border=True):
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("総商品数", f"{len(merged):,}")
    m2.metric("増加した商品", f"{(merged['増減数'] > 0).sum():,}", f"+{(merged['増減数'] > 0).sum():,}")
    m3.metric("減少した商品", f"{(merged['増減数'] < 0).sum():,}", f"-{(merged['増減数'] < 0).sum():,}")
    
    # マスター未登録の商品数をカウント
    unknown_count = merged['商品名'].astype(str).str.contains('不明：マスター未登録').sum()
    m4.metric("マスター未登録", f"{unknown_count:,}", "要確認" if unknown_count > 0 else "OK", delta_color="inverse" if unknown_count > 0 else "normal")


st.divider()

# ──────────────────────────────────────────────
# メインレイアウト分割
# ──────────────────────────────────────────────
left_col, right_col = st.columns([1, 2], gap="large")

with left_col:
    with st.container(border=True):
        st.subheader("🔍 商品名検索")
        search_query = st.text_input("検索キーワード", placeholder="商品名の一部を入力...")

        st.markdown("---")
        
        # フィルタを2カラムで配置
        f_col1, f_col2 = st.columns(2)
        
        with f_col1:
            st.subheader("🔍 在庫数フィルタ")
            filter_options = [
                "フィルタなし",
                "再入荷（過去0個→今回1個以上）",
                "新商品（今回初登場）",
                "在庫なし（0個）",
                "わずか（1〜9個）",
                "10個台（10〜19個）",
                "20個台（20〜29個）",
                "30個台（30〜39個）",
                "40個以上",
            ]
            selected_filter = st.selectbox(
                "在庫の条件",
                options=filter_options,
                index=0,
                help="選択した条件に最新の在庫数が一致する商品だけが表示されます",
            )
            
        with f_col2:
            st.subheader("📉 減少率フィルタ")
            decrease_options = [
                "指定なし",
                "減少率10%以下",
                "減少率20%以下",
                "減少率30%以下",
                "減少率40%以下",
                "減少率50%以下",
                "減少率75%以下",
            ]
            selected_decrease = st.selectbox(
                "減少の条件",
                options=decrease_options,
                index=0,
                help="指定した割合以下で減少している商品だけを表示します"
            )

        # フィルタ適用
        filtered = merged.copy()
        
        # 1. 検索キーワードで絞り込み (商品名または棚番)
        if search_query:
            filtered = filtered[
                filtered["商品名"].str.contains(search_query, case=False, na=False) |
                filtered["棚番"].str.contains(search_query, case=False, na=False)
            ]

        latest_stock = filtered[newest_col]

        # 2. 在庫数フィルタで絞り込み

        if selected_filter == "再入荷（過去0個→今回1個以上）":
            # 過去データが厳密に 0 かつ、今回が 1 以上、かつ「新商品」ではないものを抽出
            filtered = filtered[(filtered[oldest_col] == 0) & (latest_stock >= 1) & (~filtered["_is_new"])]
        elif selected_filter == "新商品（今回初登場）":
            filtered = filtered[filtered["_is_new"] & (latest_stock >= 1)]
        elif selected_filter == "在庫なし（0個）":
            filtered = filtered[latest_stock == 0]
        elif selected_filter == "わずか（1〜9個）":
            filtered = filtered[(latest_stock >= 1) & (latest_stock <= 9)]
        elif selected_filter == "10個台（10〜19個）":
            filtered = filtered[(latest_stock >= 10) & (latest_stock <= 19)]
        elif selected_filter == "20個台（20〜29個）":
            filtered = filtered[(latest_stock >= 20) & (latest_stock <= 29)]
        elif selected_filter == "30個台（30〜39個）":
            filtered = filtered[(latest_stock >= 30) & (latest_stock <= 39)]
        elif selected_filter == "40個以上":
            filtered = filtered[latest_stock >= 40]
            
        # 3. 減少率フィルタで絞り込み
        if selected_decrease == "減少率10%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 10.0]
        elif selected_decrease == "減少率20%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 20.0]
        elif selected_decrease == "減少率30%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 30.0]
        elif selected_decrease == "減少率40%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 40.0]
        elif selected_decrease == "減少率50%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 50.0]
        elif selected_decrease == "減少率75%以下":
            filtered = filtered[filtered["_decrease_rate_val"] <= 75.0]

        st.markdown("---")
        st.markdown(
            f"📌 **「{selected_filter}」** ＆ **「{selected_decrease}」** に該当する商品:  \n"
            f"**<span style='font-size:1.5rem; color:#e74c3c;'>{len(filtered)}</span>** 件 / 全 {len(merged)} 件",
            unsafe_allow_html=True
        )

        st.markdown("---")
        # PDF ダウンロード用に表示用データフレームからフラグを削除
        export_df = filtered.drop(columns=["_is_new", "_decrease_rate_val"]) if not filtered.empty else filtered

        if not export_df.empty:
            pdf_data = _create_pdf(export_df)
            st.download_button(
                label="📄 フィルタ結果を PDF でダウンロード",
                data=pdf_data,
                file_name="inventory_diff.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

with right_col:
    with st.container(border=True):
        st.subheader("📊 比較結果テーブル")

        if filtered.empty:
            st.warning("条件に該当するデータがありません。")
        else:
            display_df = filtered.drop(columns=["_is_new", "_decrease_rate_val"])
            styled = display_df.style.map(_style_diff, subset=["増減数"])
            if "棚番" in display_df.columns:
                styled = styled.apply(_highlight_warehouse_row, axis=1)
            st.dataframe(
                styled,
                use_container_width=True,
                height=600,
            )

# ──────────────────────────────────────────────
# 履歴へ保存
# ──────────────────────────────────────────────
file_names_key = tuple(f[1] for f in valid_frames)
already_saved = any(h.get("_key") == file_names_key for h in st.session_state.history)

if not already_saved:
    st.session_state.history.append(
        {
            "_key": file_names_key,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_count": len(valid_frames),
            "file_names": [f[1] for f in valid_frames],
            "product_count": len(merged),
            "dataframe": merged.drop(columns=["_is_new", "_decrease_rate_val"]).copy(),
        }
    )

# ──────────────────────────────────────────────
# 比較履歴の表示
# ──────────────────────────────────────────────
if st.session_state.history:
    st.divider()
    st.subheader("🕘 比較履歴")
    st.caption("過去にアップロードして比較した結果が保存されています（ブラウザを閉じるまで保持）。")

    for hi, h in enumerate(reversed(st.session_state.history)):
        label = f"📋 {h['timestamp']}　—　{', '.join(h['file_names'])}　（{h['product_count']} 商品）"
        with st.expander(label, expanded=(hi == 0 and not already_saved)):
            hist_df = h["dataframe"]
            hist_styled = hist_df.style.map(_style_diff, subset=["増減数"])
            if "棚番" in hist_df.columns:
                hist_styled = hist_styled.apply(_highlight_warehouse_row, axis=1)
            st.dataframe(hist_styled, use_container_width=True)
            pdf_h = _create_pdf(h["dataframe"])
            st.download_button(
                label="� この履歴を PDF でダウンロード",
                data=pdf_h,
                file_name=f"history_{h['timestamp'].replace(':', '-')}.pdf",
                mime="application/pdf",
                key=f"dl_hist_{hi}",
                use_container_width=True,
            )

    # 履歴クリアボタン
    if st.button("🗑️ 履歴をすべてクリア", use_container_width=True):
        st.session_state.history = []
        st.rerun()
