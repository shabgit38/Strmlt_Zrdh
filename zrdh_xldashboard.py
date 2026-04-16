# app.py
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date

st.set_page_config(page_title="Holdings Dashboard", layout="wide")

# ---------- READ CSV WITH MULTIPLE SYMBOLS ----------
@st.cache_data
def load_trades(path: str) -> pd.DataFrame:
    df = pd.read_excel("hldgs.xlsx") #pd.read_csv("data.csv")
    #df = pd.read_csv(path)
    # expected columns: symbol, trade_date, qty, price, ltp
    #df["trade_date"] = pd.to_datetime(df["trade_date"])
    today = pd.to_datetime(date.today())
    df["invested"] = df["Invested"]  #df["qty"] * df["price"]

    
    df["present_value"] =  df["Present value"]  # df["qty"] * df["ltp"]
    
    df["pnl"] =  df["P&L"]  #df["present_value"] - df["invested"]
    
    df["pnl_pct"] =   df["P&L chg"] #(df["pnl"] / df["invested"]) * 100
    #df["age_days"] = (today - df["trade_date"]).dt.days
    
    df["sector"] = df["SECTOR"]
    return df

# local file path or use st.file_uploader
DATA_PATH = "hldgs.csv"
df_all = load_trades(DATA_PATH)

# ------------- SYMBOL SELECTION (MULTI-STOCK) -------------
symbols = sorted(df_all["symbol"].unique())
selected_symbol = st.sidebar.selectbox("Select symbol", symbols)

df_symbol = df_all[df_all["symbol"] == selected_symbol].copy()

# ------------- SUMMARY METRICS FOR SELECTED SYMBOL -------------
st.title("Stock Holding Dashboard")
st.subheader(f"{selected_symbol} – Position Summary")

total_qty = int(df_symbol["qty"].sum())
total_invested = df_symbol["invested"].sum()
present_value = df_symbol["present_value"].sum()
avg_buy = (total_invested / total_qty) if total_qty else 0
ltp = float(df_symbol["ltp"].iloc[0]) if not df_symbol.empty else 0
total_pnl = present_value - total_invested
total_pnl_pct = (total_pnl / total_invested) * 100 if total_invested else 0

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Quantity", f"{total_qty}")
c2.metric("Avg Buy", f"{avg_buy:,.2f}")
c3.metric("Invested", f"{total_invested:,.2f}")
c4.metric("LTP", f"{ltp:,.2f}")
c5.metric("Present Value", f"{present_value:,.2f}")
c6.metric("Total P&L", f"{total_pnl:,.2f}", f"{total_pnl_pct:,.2f}%")
c7.metric("Lots", f"{len(df_symbol)}")
# st.metric usage and behavior: [web:13][web:16]

# ------------- TRANSACTION TABLE -------------
st.markdown("### Transaction Breakdown")

display_cols = df_symbol.copy()
display_cols["Date"] = display_cols["trade_date"].dt.date
display_cols = display_cols[
    ["Date", "qty", "price", "invested", "age_days",
     "ltp", "present_value", "pnl", "pnl_pct"]
].rename(
    columns={
        "qty": "Qty",
        "price": "Buy Price",
        "invested": "Buy Value",
        "age_days": "Holding (days)",
        "ltp": "LTP",
        "present_value": "Present Value",
        "pnl": "P&L",
        "pnl_pct": "P&L %",
    }
)

st.data_editor(
    display_cols,
    use_container_width=True,
    hide_index=True,
)
# st.data_editor docs: [web:18][web:21]

# ------------- HOLDING PERIOD VS RETURN CHART -------------
st.markdown("### Holding Period vs Return")

if not df_symbol.empty:
    fig = px.scatter(
        df_symbol,
        x="age_days",
        y="pnl_pct",
        size="qty",
        color=df_symbol["pnl"].apply(lambda x: "Profit" if x >= 0 else "Loss"),
        hover_data=["trade_date", "qty", "price", "invested", "pnl", "pnl_pct"],
        labels={"age_days": "Holding Period (days)", "pnl_pct": "P&L %"},
    )
    st.plotly_chart(fig, use_container_width=True)
# plotly in Streamlit: [web:19][web:22]

# ------------- ENTRY / EXIT FILTERS -------------
st.markdown("### Entry / Exit Candidates")

col_left, col_right = st.columns(2)
with col_left:
    target_exit = st.number_input("Exit if P&L % ≥", value=50.0, step=5.0)
    target_cut = st.number_input("Cut loss if P&L % ≤", value=-10.0, step=1.0)

df_symbol["tag"] = df_symbol["pnl_pct"].apply(
    lambda x: "Exit" if x >= target_exit
    else ("Cut Loss" if x <= target_cut else "Hold")
)

with col_right:
    st.write(df_symbol["tag"].value_counts())

tab1, tab2, tab3 = st.tabs(["Exit", "Cut Loss", "Hold"])

def show_tag(tag, container):
    subset = df_symbol[df_symbol["tag"] == tag]
    if subset.empty:
        container.info(f"No {tag} candidates.")
    else:
        container.dataframe(
            subset[["trade_date", "qty", "price", "age_days", "pnl", "pnl_pct"]],
            use_container_width=True,
        )

with tab1:
    show_tag("Exit", st)
with tab2:
    show_tag("Cut Loss", st)
with tab3:
    show_tag("Hold", st)
