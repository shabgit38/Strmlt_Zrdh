# Stock Holdings Dashboard - Accordion Grid

## Overview
A comprehensive Streamlit dashboard that displays stock holdings in an interactive accordion grid format with expandable and collapsible sections organized by sectors.

## Features

### 📊 **Portfolio Summary**
- Total holdings count and percentage
- Total invested amount with Indian numbering format (Cr/L/K)
- Current portfolio value
- Total P&L (absolute and percentage)
- Average P&L percentage

### 🏢 **Sector-wise Holdings**
- Grouped view by sectors (Banking, IT, FMCG, etc.)
- Sector-wise investment breakdown
- Current value and P&L for each sector
- Interactive data table with formatted currency values

### 🔍 **Accordion Grid View**
- **Expandable/Collapsible sectors**: Click sector headers to show/hide stocks
- **Grid layout**: 3 stocks per row for optimal space utilization
- **Individual stock cards**: Each stock displays:
  - Symbol and sector
  - Invested amount
  - Current value
  - Last Traded Price (LTP)
  - Profit/Loss (absolute and percentage)
  - Color-coded P&L indicators (green for profit, red for loss)

### 📈 **Portfolio Insights**
- **Top Gainers**: Stocks with highest P&L percentage
- **Top Losers**: Stocks with lowest P&L percentage
- Visual indicators for performance tracking

### 🔧 **Interactive Features**
- **Sector filtering**: Multi-select to filter by sectors
- **Symbol search**: Real-time search by stock symbol
- **Data download**: Export filtered data to CSV
- **Responsive design**: Adapts to different screen sizes

## Sample Columns Displayed

| Column | Description |
|--------|-------------|
| **Symbol** | Stock ticker symbol |
| **Sector** | Industry sector classification |
| **Invested** | Total amount invested in the stock |
| **Current Value** | Present market value of holdings |
| **LTP** | Last Traded Price |
| **P&L** | Absolute profit/loss amount |
| **P&L %** | Percentage change in value |
| **Stock Count** | Number of stocks in each sector |

## Technical Implementation

### Dependencies
- `streamlit` - Dashboard framework
- `pandas` - Data manipulation
- `numpy` - Numerical operations

### Key Components
- **st.expander()** - Creates accordion functionality
- **st.columns()** - Grid layout for stock cards
- **Custom CSS** - Enhanced styling and visual appeal
- **@st.cache_data** - Performance optimization for data loading

### Data Format
The dashboard expects an Excel file (`hldgs.xlsx`) with the following columns:
- `symbol` - Stock symbol
- `SECTOR` - Sector classification
- `Invested` - Amount invested
- `Present value` - Current value
- `P&L` - Profit/Loss amount
- `P&L chg` - Profit/Loss percentage
- `ltp` - Last Traded Price

## Usage

1. **Start the dashboard**:
   ```bash
   python -m streamlit run zrdh_accordion_dashboard.py
   ```

2. **Access the dashboard**: Open your browser and navigate to `http://localhost:8501`

3. **Interact with the dashboard**:
   - Use sidebar filters to refine your view
   - Click sector headers to expand/collapse holdings
   - Search for specific stocks by symbol
   - Download filtered data as CSV

## Screenshots

The dashboard provides:
- A clean, professional interface
- Color-coded performance indicators
- Responsive grid layout
- Interactive filtering and search capabilities
- Download functionality for data export

## Customization

The dashboard can be easily customized by:
- Modifying the CSS styles in the `<style>` block
- Adding new columns to the data display
- Changing the grid layout (number of columns)
- Adding new filtering options
- Integrating with different data sources

## Sample Data

A sample data file (`sample_holdings.xlsx`) is included with realistic stock holding data across multiple sectors for testing and demonstration purposes.