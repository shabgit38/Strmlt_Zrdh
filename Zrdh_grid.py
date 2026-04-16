import streamlit as st
import pandas as pd
import numpy as np

# 1. Setup Sample Data
# We simulate the nested structure from your image
data = [
    {
        "Symbol": "ASHOKLEY", "Total_Qty": 200, "Avg_Price": 58.58, "LTP": 190.65, 
        "Trend": [180, 185, 182, 188, 190, 190.65],
        "Transactions": [
            {"Date": "2025-07-16", "Qty": 100, "Price": 0, "Age": 196},
            {"Date": "2022-03-03", "Qty": 6, "Price": 110, "Age": 1426},
        ]
    },
    {
        "Symbol": "BEL", "Total_Qty": 15, "Avg_Price": 372.00, "LTP": 413.00, 
        "Trend": [380, 390, 405, 410, 413],
        "Transactions": [
            {"Date": "2025-08-26", "Qty": 10, "Price": 365.5, "Age": 154},
            {"Date": "2025-07-28", "Qty": 5, "Price": 385.0, "Age": 183},
        ]
    }
]

st.set_page_config(layout="wide")
st.title("📈 Portfolio Master-Detail Dashboard")

# 2. Header Style Row
header_cols = st.columns([1, 0.5, 0.5, 0.5, 0.5, 0.5])
fields = ["Symbol", "Total Qty", "Avg Price", "LTP", "PnL%", ""]
for col, field in zip(header_cols, fields):
    col.write(f"**{field}**")

st.divider()

# 3. Create the "Grid" using Expanders
for item in data:
    # state key for this row
    key = f"exp_{item['Symbol']}"
    if key not in st.session_state:
        st.session_state[key] = False

    # Header shown outside the expander as columns
    pnl_pct = ((item['LTP'] - item['Avg_Price']) / item['Avg_Price']) * 100
    color = "green" if pnl_pct > 0 else "red"

    header_cols = st.columns([1, 0.5, 0.5, 0.5, 0.5, 0.5])
    header_cols[0].markdown(f"**{item['Symbol']}**")
    header_cols[1].write(f"{item['Total_Qty']}")
    header_cols[2].write(f"₹{item['Avg_Price']:.2f}")
    header_cols[3].write(f"₹{item['LTP']:.2f}")
    #header_cols[4].write("")  # sparkline slot (populate if needed)
    header_cols[4].markdown(f":{color}[{pnl_pct:+.2f}%]")

    # Toggle button in the last column to open/close the expander
    #if header_cols[5].button("Expand", key=key + "_btn"):
    #   st.session_state[key] = not st.session_state[key]

    # The actual expander holds the detail content (starts closed by default)
    with st.expander("", expanded=st.session_state[key]):
        # ...existing detail code...
        #st.markdown("#### 📝 Transaction Breakdown")
        detail_df = pd.DataFrame(item['Transactions'])
        st.dataframe(
            detail_df,
            column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "Age": st.column_config.NumberColumn(format="%d days ⏳")
            },
            width='stretch',
            hide_index=True
        )