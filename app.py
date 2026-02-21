import streamlit as st
import pandas as pd
import hashlib
from datetime import datetime

# ──────────────────────────────────────────────
# ページ設定
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="在庫増減チェッカー",
    page_icon="📦",
    layout="wide",
)

# ──────────────────────────────────────────────
# 認証設定（ID・パスワード）
# ──────────────────────────────────────────────
# パスワードは SHA-256 ハッシュで保存。
# 新しいユーザーを追加するには、下記の辞書にエントリを追加してください。
# ハッシュ値は Python で以下のように生成できます:
#   import hashlib
#   hashlib.sha256("あなたのパスワード".encode()).hexdigest()
_USERS = {
    "admin": hashlib.sha256("admin123".encode()).hexdigest(),
    "400476": hashlib.sha256("230915".encode()).hexdigest(),
    # "user2": hashlib.sha256("password2".encode()).hexdigest(),
}


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
# カスタム CSS
# ──────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* ヘッダー */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        color: white;
        text-align: center;
    }
    .main-header h1 { margin: 0; font-size: 2rem; }
    .main-header p  { margin: 0.3rem 0 0 0; opacity: 0.85; font-size: 1rem; }

    /* 増減セル色 */
    .positive { color: #2ecc71; font-weight: 700; }
    .negative { color: #e74c3c; font-weight: 700; }
    .zero     { color: #95a5a6; }

    /* データフレーム幅 */
    .stDataFrame { width: 100% !important; }

    /* 履歴カード */
    .history-card {
        background: linear-gradient(135deg, #f5f7fa, #c3cfe2);
        border-radius: 10px;
        padding: 0.8rem 1.2rem;
        margin-bottom: 0.6rem;
        border-left: 4px solid #667eea;
    }
    .history-card h4 { margin: 0 0 0.3rem 0; color: #2d3748; font-size: 0.95rem; }
    .history-card p  { margin: 0; color: #4a5568; font-size: 0.82rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# ヘッダー
# ──────────────────────────────────────────────
st.markdown(
    '<div class="main-header">'
    "<h1>📦 在庫増減チェッカー</h1>"
    "<p>複数の Excel ファイルをアップロードして、商品ごとの在庫増減を一覧比較できます</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# 列名の自動検出ヘルパー
# ──────────────────────────────────────────────
_PRODUCT_CANDIDATES = ["商品名", "品名", "製品名", "品番", "商品", "アイテム名", "item", "product"]
_QTY_CANDIDATES = ["個数", "数量", "在庫数", "在庫", "stock", "quantity", "qty"]


def _find_column(columns: pd.Index, candidates: list[str]) -> str | None:
    """大文字小文字を無視して候補名に一致する列を返す。"""
    lower_map = {c.strip().lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


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
                df = pd.read_excel(file, engine="openpyxl")
                uploaded_files.append((file.name, df, None))
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
        st.warning(f"⚠️ {idx + 1} 番目のファイル（{name}）の読み込みに失敗しました: {err}")
        continue

    # 列の検出
    product_col = _find_column(df.columns, _PRODUCT_CANDIDATES)
    qty_col = _find_column(df.columns, _QTY_CANDIDATES)

    if product_col is None:
        st.warning(
            f"⚠️ {idx + 1} 番目のファイル（{name}）に「商品名」に該当する列が見つかりません。"
            f"\n  検出対象: {', '.join(_PRODUCT_CANDIDATES)}"
            f"\n  実際の列名: {', '.join(df.columns.tolist())}"
        )
        continue

    if qty_col is None:
        st.warning(
            f"⚠️ {idx + 1} 番目のファイル（{name}）に「個数」に該当する列が見つかりません。"
            f"\n  検出対象: {', '.join(_QTY_CANDIDATES)}"
            f"\n  実際の列名: {', '.join(df.columns.tolist())}"
        )
        continue

    extracted = df[[product_col, qty_col]].copy()
    extracted.columns = ["商品名", "個数"]
    extracted["個数"] = pd.to_numeric(extracted["個数"], errors="coerce").fillna(0).astype(int)
    extracted = extracted.dropna(subset=["商品名"])
    extracted = extracted.groupby("商品名", as_index=False)["個数"].sum()

    valid_frames.append((idx + 1, name, extracted))

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

# 結合
merged = valid_frames[0][2][["商品名"]].copy()
col_labels: list[str] = []

for file_no, fname, frame in valid_frames:
    col_name = f"{file_no}番目({fname})"
    col_labels.append(col_name)
    merged = merged.merge(
        frame.rename(columns={"個数": col_name}),
        on="商品名",
        how="outer",
    )

# NaN を 0 に
for c in col_labels:
    merged[c] = merged[c].fillna(0).astype(int)

# 増減列
oldest_col = col_labels[0]
newest_col = col_labels[-1]
merged["増減数"] = merged[newest_col] - merged[oldest_col]

# ソート
merged = merged.sort_values("商品名").reset_index(drop=True)

# ──────────────────────────────────────────────
# フィルタリング — 増減数スライダー
# ──────────────────────────────────────────────
st.subheader("🔍 増減数フィルタ")

diff_min = int(merged["増減数"].min())
diff_max = int(merged["増減数"].max())

if diff_min == diff_max:
    # 全商品の増減が同じ場合はスライダー不要
    st.info(f"すべての商品の増減数が **{diff_min}** です。")
    selected_diff = diff_min
    filtered = merged.copy()
else:
    selected_diff = st.slider(
        "増減数を指定してください",
        min_value=diff_min,
        max_value=diff_max,
        value=diff_min,
        step=1,
        help="スライダーを動かすと、その増減数に一致する商品だけが表示されます",
    )

    # リアルタイムで該当件数を表示
    match_count = int((merged["増減数"] == selected_diff).sum())
    total_count = len(merged)

    st.markdown(
        f"📌 増減数 **{selected_diff:+d}** に該当する商品: "
        f"**{match_count}** 件 / 全 {total_count} 件"
    )

    # フィルタ適用
    filtered = merged[merged["増減数"] == selected_diff].copy()

# ──────────────────────────────────────────────
# テーブル表示
# ──────────────────────────────────────────────
st.subheader("📊 比較結果")

# サマリーカード
m1, m2, m3, m4 = st.columns(4)
m1.metric("商品数（フィルタ後）", f"{len(filtered):,}")
m2.metric("増加した商品", f"{(filtered['増減数'] > 0).sum():,}")
m3.metric("減少した商品", f"{(filtered['増減数'] < 0).sum():,}")
m4.metric("変化なし", f"{(filtered['増減数'] == 0).sum():,}")


# 色付け関数
def _style_diff(val):
    if val > 0:
        return "color: #27ae60; font-weight: 700"
    elif val < 0:
        return "color: #e74c3c; font-weight: 700"
    return "color: #95a5a6"


if filtered.empty:
    st.warning("条件に該当するデータがありません。")
else:
    styled = filtered.style.map(_style_diff, subset=["増減数"])
    st.dataframe(
        styled,
        use_container_width=True,
        height=min(len(filtered) * 38 + 50, 600),
    )

    # CSV ダウンロード
    csv_data = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 結果を CSV でダウンロード",
        data=csv_data,
        file_name="inventory_diff.csv",
        mime="text/csv",
        use_container_width=True,
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
            "dataframe": merged.copy(),
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
            st.dataframe(h["dataframe"], use_container_width=True)
            csv_h = h["dataframe"].to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 この履歴を CSV でダウンロード",
                data=csv_h,
                file_name=f"history_{h['timestamp'].replace(':', '-')}.csv",
                mime="text/csv",
                key=f"dl_hist_{hi}",
                use_container_width=True,
            )

    # 履歴クリアボタン
    if st.button("🗑️ 履歴をすべてクリア", use_container_width=True):
        st.session_state.history = []
        st.rerun()
