"""
Rental Utility Bill Splitter
-----------------------------
A Streamlit app that splits combined electricity + water utility bills
across 13 rented rooms.

How to run:
    1. pip install streamlit pandas numpy
    2. streamlit run rental_bill_splitter.py

Business logic (as provided):
    - 13 rooms, each with its own electricity meter (previous/this month reading).
    - 3 physical bills are received:
        * waterBill -> covers electricity for rooms 11, 12, 13 AND water for all 13 rooms
        * bill2, bill3 -> pure electricity for the other rooms
    - Water-only units = unitsConsumedInWaterBill - (units consumed by rooms 11+12+13)
    - Each "consumer" (13 rooms + 1 virtual "Water" row) has units split into:
        * Base units (up to a cap, default 100) at a flat rate (default Rs. 5/unit)
        * Extra units, priced at a blended rate = (totalBill - sum of all Base Price) / (sum of all Extra units)
    - The Water row's Base + Extra price = total water bill, which is then
      redistributed across the 13 rooms by headcount (people per room).
    - Final Bill per room = its own Base Price + Extra Price + its Water Bill share,
      rounded UP to the nearest rupee.
"""

import streamlit as st
import pandas as pd
import numpy as np
import streamlit.components.v1 as components

ROOMS = list(range(1, 14))
ELECTRICITY_ROOMS_IN_WATER_BILL = (11, 12, 13)

st.set_page_config(page_title="Rental Utility Bill Splitter", layout="wide")

st.title("🏠 Rental Utility Bill Splitter")
st.caption(
    "Enter meter readings, occupancy, and bill amounts below. "
    "The app calculates each room's final share automatically."
)

if "result_df" not in st.session_state:
    st.session_state.result_df = None

# ---------------------------------------------------------------------------
# INPUT FORM
# ---------------------------------------------------------------------------
with st.form("bill_form"):
    st.subheader("1. Room Meter Readings & Occupancy")
    st.write(
        "Rooms 11, 12 and 13's electricity is billed together with the water bill. "
        "All fields below are plain number boxes, so phones show the numeric keypad."
    )

    header = st.columns([1, 2, 2, 1.5])
    header[0].markdown("**Room**")
    header[1].markdown("**Previous Month**")
    header[2].markdown("**This Month**")
    header[3].markdown("**People**")

    previousMonth = {}
    thisMonth = {}
    numberOfPeopleAsPerHouse = {}

    for room in ROOMS:
        c1, c2, c3, c4 = st.columns([1, 2, 2, 1.5])
        c1.markdown(f"**{room}**")
        previousMonth[room] = c2.number_input(
            f"Previous Month Units - Room {room}",
            min_value=0, value=0, step=1,
            key=f"prev_{room}", label_visibility="collapsed",
        )
        thisMonth[room] = c3.number_input(
            f"This Month Units - Room {room}",
            min_value=0, value=0, step=1,
            key=f"this_{room}", label_visibility="collapsed",
        )
        numberOfPeopleAsPerHouse[room] = c4.number_input(
            f"Number of People - Room {room}",
            min_value=0, value=1, step=1,
            key=f"people_{room}", label_visibility="collapsed",
        )

    st.subheader("2. Utility Bills")
    col1, col2, col3 = st.columns(3)
    with col1:
        waterBill = st.number_input(
            "Water Bill (Rs.)",
            min_value=0, value=0, step=1,
            help="Includes electricity for rooms 11, 12, 13 and water for all rooms.",
        )
    with col2:
        bill2 = st.number_input("Electricity Bill 2 (Rs.)", min_value=0, value=0, step=1)
    with col3:
        bill3 = st.number_input("Electricity Bill 3 (Rs.)", min_value=0, value=0, step=1)

    unitsConsumedInWaterBill = st.number_input(
        "Units Consumed shown on Water Bill",
        min_value=0, value=0, step=1,
        help="Combined meter units (electricity for 11/12/13 + water for all rooms).",
    )

    st.subheader("3. Pricing Rules")
    col4, col5 = st.columns(2)
    with col4:
        base_cap = st.number_input("Base unit cap", min_value=0, value=100, step=1)
    with col5:
        base_rate = st.number_input("Base rate per unit (Rs.)", min_value=0.0, value=5.0, step=0.5)

    submitted = st.form_submit_button("Calculate", use_container_width=True)

# ---------------------------------------------------------------------------
# CALCULATION
# ---------------------------------------------------------------------------
if submitted:
    try:
        # Basic sanity check
        bad_rooms = [r for r in ROOMS if thisMonth[r] < previousMonth[r]]
        if bad_rooms:
            st.error(
                f"Room(s) {bad_rooms}: 'This Month Units' is less than 'Previous Month Units'. "
                "Please check the readings."
            )
            st.stop()

        totalBill = waterBill + bill2 + bill3

        elec_units_in_water_bill = sum(
            thisMonth[r] - previousMonth[r] for r in ELECTRICITY_ROOMS_IN_WATER_BILL
        )
        totalWaterUnits = unitsConsumedInWaterBill - elec_units_in_water_bill

        # Build per-room rows
        data = []
        for room in ROOMS:
            previous_units = previousMonth[room]
            this_units = thisMonth[room]
            units_consumed = this_units - previous_units
            data.append(
                {
                    "Room Number": room,
                    "Number of People": numberOfPeopleAsPerHouse[room],
                    "Previous Month Units": previous_units,
                    "This Month Units": this_units,
                    "Units Consumed": units_consumed,
                }
            )

        # Virtual "Water" row
        data.append(
            {
                "Room Number": "Water",
                "Number of People": 0,
                "Previous Month Units": None,
                "This Month Units": None,
                "Units Consumed": totalWaterUnits,
            }
        )

        df = pd.DataFrame(data)

        df["Base Units"] = df["Units Consumed"].apply(lambda x: min(base_cap, x))
        df["Extra Units"] = df["Units Consumed"] - df["Base Units"]
        df["Base Price"] = df["Base Units"] * base_rate

        extraBill = totalBill - df["Base Price"].sum()
        totalExtraUnits = df["Extra Units"].sum()
        amountPerExtraUnit = (extraBill / totalExtraUnits) if totalExtraUnits else 0.0

        df["Extra Price"] = amountPerExtraUnit * df["Extra Units"]

        water_row = df[df["Room Number"] == "Water"].iloc[0]
        totalWaterBill = water_row["Base Price"] + water_row["Extra Price"]

        total_people = df["Number of People"].sum()
        waterBillPerPerson = (totalWaterBill / total_people) if total_people else 0.0

        df["Water Bill"] = waterBillPerPerson * df["Number of People"]

        df = df[df["Room Number"] != "Water"].reset_index(drop=True)

        df["Total Bill"] = df["Base Price"] + df["Extra Price"] + df["Water Bill"]
        df["Final Price"] = np.ceil(df["Total Bill"]).astype(int)

        st.session_state.result_df = df[["Room Number", "Final Price"]].copy()
        st.session_state.full_df = df.copy()
        st.session_state.totalBill = totalBill
        st.session_state.totalWaterUnits = totalWaterUnits

    except Exception as e:
        st.error(f"Something went wrong in the calculation: {e}")
        st.session_state.result_df = None

# ---------------------------------------------------------------------------
# RESULTS
# ---------------------------------------------------------------------------
if st.session_state.result_df is not None:
    st.divider()
    st.subheader("📋 Final Bill per Room")

    result_display = st.session_state.result_df.rename(
        columns={"Final Price": "Amount Due (Rs.)"}
    ).set_index("Room Number")

    st.table(result_display)

    total_collected = int(st.session_state.result_df["Final Price"].sum())
    rounding_buffer = round(total_collected - st.session_state.totalBill, 2)
    c1, c2 = st.columns(2)
    c1.metric("Total Bill (actual)", f"Rs. {st.session_state.totalBill:,.2f}")
    c2.metric("Total Collected (after rounding up)", f"Rs. {total_collected:,}")
    st.caption(f"Rounding buffer collected: Rs. {rounding_buffer:,.2f}")

    with st.expander("See detailed breakdown (base / extra / water share per room)"):
        st.dataframe(st.session_state.full_df, use_container_width=True)

    st.divider()
    if st.button("🖨️ Print / Save as PDF"):
        components.html("<script>window.parent.print();</script>", height=0)
    st.caption(
        "Clicking Print opens your browser's print dialog — choose "
        "'Save as PDF' as the destination to export the table."
    )

    # Hide everything except the results table & totals when printing
    st.markdown(
        """
        <style>
        @media print {
            header, [data-testid="stSidebar"], [data-testid="stForm"],
            .stButton, [data-testid="stExpander"], figcaption {
                display: none !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
