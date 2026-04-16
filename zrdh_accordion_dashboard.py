import streamlit as st
import pandas as pd
from datetime import datetime
import numpy as np

# Set page configuration
st.set_page_config(
    page_title="Stock Holdings Dashboard - Accordion Grid",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .stock-card {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 15px;
        margin-bottom: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .profit-text {
        color: #28a745;
        font-weight: bold;
    }
    .loss-text {
        color: #dc3545;
        font-weight: bold;
    }
    .sector-header {
        font-size: 18px;
        font-weight: bold;
        color: #495057;
        border-bottom: 2px solid #dee2e6;
        padding-bottom: 10px;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_data():
    """Load and preprocess the holdings data"""
    try:
        # Try to load the original file first
        df = pd.read_excel("hldgs.xlsx")
        if df.empty or len(df) == 0:
            # If original file is empty, use sample data
            df = pd.read_excel("sample_holdings.xlsx")
        return df
    except FileNotFoundError:
        # If files don't exist, create sample data
        return create_sample_data()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return create_sample_data()

def create_sample_data():
    """Create sample data when Excel files are not available"""
    import numpy as np
    np.random.seed(42)
    sample_data = {
        'symbol': ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'AXISBANK', 'KOTAKBANK', 'LT', 'ITC'],
        'SECTOR': ['Energy', 'IT', 'IT', 'Banking', 'Banking', 'Banking', 'Banking', 'Banking', 'Construction', 'FMCG'],
        'Invested': np.random.randint(50000, 500000, 10),
        'Present value': np.random.randint(40000, 600000, 10),
        'P&L': np.random.randint(-50000, 100000, 10),
        'P&L chg': np.random.uniform(-20, 30, 10),
        'ltp': np.random.uniform(100, 3000, 10)
    }
    return pd.DataFrame(sample_data)

def format_currency(value):
    """Format currency values with Indian numbering style"""
    if pd.isna(value):
        return "₹0"
    if abs(value) >= 10000000:
        return f"₹{value/10000000:.2f} Cr"
    elif abs(value) >= 100000:
        return f"₹{value/100000:.2f} L"
    elif abs(value) >= 1000:
        return f"₹{value/1000:.2f} K"
    else:
        return f"₹{value:.2f}"

def format_percentage(value):
    """Format percentage values with color coding"""
    if pd.isna(value):
        return "0.00%"
    if value >= 0:
        return f'<span class="profit-text">+{value:.2f}%</span>'
    else:
        return f'<span class="loss-text">{value:.2f}%</span>'

def main():
    st.title("📈 Stock Holdings Dashboard - Accordion Grid View")
    st.markdown("### Interactive portfolio management with expandable sector-wise view")
    
    # Load data
    df = load_data()
    
    if df.empty:
        st.warning("No data available. Please check your Excel file.")
        return
    
    # Sidebar filters
    st.sidebar.header("📊 Filters & Settings")
    
    # Date information
    st.sidebar.subheader("Data Information")
    st.sidebar.write(f"**Total Holdings:** {len(df)}")
    st.sidebar.write(f"**Sectors:** {df['SECTOR'].nunique()}")
    
    # Sector filter
    sectors = sorted(df['SECTOR'].dropna().unique())
    selected_sectors = st.sidebar.multiselect(
        "Filter by Sector", 
        sectors, 
        default=sectors
    )
    
    # Symbol search
    search_symbol = st.sidebar.text_input("Search by Symbol", "").upper()
    
    # Apply filters
    filtered_df = df.copy()
    if selected_sectors:
        filtered_df = filtered_df[filtered_df['SECTOR'].isin(selected_sectors)]
    if search_symbol:
        filtered_df = filtered_df[filtered_df['symbol'].str.contains(search_symbol, na=False)]
    
    # Portfolio summary
    st.markdown("---")
    st.markdown("### 📊 Portfolio Summary")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric(
            "Total Holdings", 
            len(filtered_df),
            f"{len(filtered_df)/len(df)*100:.1f}%" if len(df) > 0 else "0%"
        )
    
    with col2:
        total_invested = filtered_df['Invested'].sum()
        st.metric("Total Invested", format_currency(total_invested))
    
    with col3:
        total_value = filtered_df['Present value'].sum()
        st.metric("Current Value", format_currency(total_value))
    
    with col4:
        total_pnl = filtered_df['P&L'].sum()
        pnl_pct = (total_pnl / total_invested * 100) if total_invested != 0 else 0
        st.metric(
            "Total P&L", 
            format_currency(total_pnl),
            f"{pnl_pct:.2f}%"
        )
    
    with col5:
        avg_pnl_pct = filtered_df['P&L chg'].mean()
        st.metric("Avg P&L %", f"{avg_pnl_pct:.2f}%")
    
    # Sector-wise breakdown
    st.markdown("---")
    st.markdown("### 🏢 Sector-wise Holdings")
    
    # Group by sector
    sector_data = filtered_df.groupby('SECTOR').agg({
        'Invested': 'sum',
        'Present value': 'sum',
        'P&L': 'sum',
        'P&L chg': 'mean',
        'symbol': 'count'
    }).round(2)
    sector_data.columns = ['Total Invested', 'Current Value', 'Total P&L', 'Avg P&L %', 'Stock Count']
    sector_data['P&L %'] = (sector_data['Total P&L'] / sector_data['Total Invested'] * 100).round(2)
    
    # Display sector summary
    st.dataframe(
        sector_data.style.format({
            'Total Invested': '₹{:,}',
            'Current Value': '₹{:,}',
            'Total P&L': '₹{:,}',
            'Avg P&L %': '{:.2f}%',
            'P&L %': '{:.2f}%',
            'Stock Count': '{}'
        }),
        use_container_width=True
    )
    
    # Accordion Grid View
    st.markdown("---")
    st.markdown("### 🔍 Detailed Holdings (Accordion Grid)")
    
    # Get unique sectors in filtered data
    sectors_in_view = sorted(filtered_df['SECTOR'].dropna().unique())
    
    for sector in sectors_in_view:
        sector_stocks = filtered_df[filtered_df['SECTOR'] == sector].copy()
        
        # Create expander for each sector
        with st.expander(f"🏢 {sector} Sector ({len(sector_stocks)} stocks)", expanded=False):
            
            # Sector summary
            sector_total_invested = sector_stocks['Invested'].sum()
            sector_total_value = sector_stocks['Present value'].sum()
            sector_total_pnl = sector_stocks['P&L'].sum()
            sector_pnl_pct = (sector_total_pnl / sector_total_invested * 100) if sector_total_invested != 0 else 0
            
            st.markdown(f"**Sector Summary:** Invested: {format_currency(sector_total_invested)} | "
                       f"Value: {format_currency(sector_total_value)} | "
                       f"P&L: {format_currency(sector_total_pnl)} ({sector_pnl_pct:.2f}%)")
            
            # Create grid layout for stocks
            cols = st.columns(3)  # 3 stocks per row
            
            for idx, (_, stock) in enumerate(sector_stocks.iterrows()):
                col_idx = idx % 3
                with cols[col_idx]:
                    # Determine P&L color
                    pnl_color = "profit-text" if stock['P&L'] >= 0 else "loss-text"
                    
                    st.markdown(f"""
                    <div class="stock-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h4 style="margin: 0; font-size: 16px;">{stock['symbol']}</h4>
                            <span style="font-size: 12px; color: #6c757d;">{stock['SECTOR']}</span>
                        </div>
                        <hr style="margin: 10px 0; border: none; border-top: 1px solid #dee2e6;">
                        <div style="font-size: 14px;">
                            <div style="display: flex; justify-content: space-between;">
                                <span>Invested:</span>
                                <span style="font-weight: bold;">{format_currency(stock['Invested'])}</span>
                            </div>
                            <div style="display: flex; justify-content: space-between;">
                                <span>Current Value:</span>
                                <span style="font-weight: bold;">{format_currency(stock['Present value'])}</span>
                            </div>
                            <div style="display: flex; justify-content: space-between;">
                                <span>LTP:</span>
                                <span style="font-weight: bold;">₹{stock['ltp']:.2f}</span>
                            </div>
                            <div style="display: flex; justify-content: space-between; margin-top: 8px;">
                                <span style="font-weight: bold;">P&L:</span>
                                <span class="{pnl_color}" style="font-weight: bold;">{format_currency(stock['P&L'])}</span>
                            </div>
                            <div style="display: flex; justify-content: space-between;">
                                <span>P&L %:</span>
                                <span class="{pnl_color}" style="font-weight: bold;">{stock['P&L chg']:.2f}%</span>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
    
    # Additional insights
    st.markdown("---")
    st.markdown("### 📈 Portfolio Insights")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Top gainers
        top_gainers = filtered_df.nlargest(5, 'P&L chg')[['symbol', 'P&L chg', 'P&L']]
        st.markdown("#### 🏆 Top Gainers")
        for _, stock in top_gainers.iterrows():
            st.markdown(f"- **{stock['symbol']}**: +{stock['P&L chg']:.2f}% ({format_currency(stock['P&L'])})")
    
    with col2:
        # Top losers
        top_losers = filtered_df.nsmallest(5, 'P&L chg')[['symbol', 'P&L chg', 'P&L']]
        st.markdown("#### 💸 Top Losers")
        for _, stock in top_losers.iterrows():
            st.markdown(f"- **{stock['symbol']}**: {stock['P&L chg']:.2f}% ({format_currency(stock['P&L'])})")
    
    # Download filtered data
    st.markdown("---")
    csv = filtered_df.to_csv(index=False)
    st.download_button(
        label="📥 Download Filtered Data",
        data=csv,
        file_name=f"filtered_holdings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )

if __name__ == "__main__":
    main()