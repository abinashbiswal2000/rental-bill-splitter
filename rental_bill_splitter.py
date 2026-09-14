"""
Rental Utility Bill Splitter
-----------------------------
A Streamlit app that splits combined electricity + water utility bills
across 13 rented rooms.

How to run:
    1. pip install -r Requirements.txt
    2. streamlit run p2.py

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
      rounded DOWN to the nearest rupee.

Persistence model:
    Nothing is written to disk. All history lives in an .xlsx you upload at the
    start (Calculate tab) and download again after committing this month's data
    (View Data tab), so the app works the same whether run locally or on an
    ephemeral host. The workbook has 3 sheets:
        - Readings:       rows R1 Reading..R13 Reading, WaterBill, WaterBillUnits,
                           ElectricityBill-2, ElectricityBill-3 (one column per month)
        - AmoundPaid:      rows R1 Amount..R13 Amount (one column per month)
        - NumberOfPeople:  single column "NumberOfPeople", rows R1 People..R13 People
"""

import io
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime


ROOMS = list(range(1, 14))
ELECTRICITY_ROOMS_IN_WATER_BILL = (11, 12, 13)

READINGS_SHEET = "Readings"
AMOUNDPAID_SHEET = "AmoundPaid"
PEOPLE_SHEET = "NumberOfPeople"
PEOPLE_COLUMN = "NumberOfPeople"

READINGS_ROOM_ROWS = [f"R{r} Reading" for r in ROOMS]
READINGS_BILL_ROWS = ["WaterBill", "WaterBillUnits", "ElectricityBill-2", "ElectricityBill-3"]
READINGS_ROWS = READINGS_ROOM_ROWS + READINGS_BILL_ROWS
AMOUNDPAID_ROWS = [f"R{r} Amount" for r in ROOMS]
PEOPLE_ROWS = [f"R{r} People" for r in ROOMS]

SECTIONS = ["🧮 Calculate", "📊 View Data", "✏️ Edit Data", "🔢 Full Calculation"]

st.set_page_config(page_title="Rental Utility Bill Splitter", layout="wide")

# Cap the whole page's content width and center it, instead of letting every
# widget stretch edge-to-edge on wide screens.
st.markdown(
    """
    <style>
    .block-container {
        max-width: 1100px;
        margin: 0 auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def _safe_int(value):
    return None if pd.isna(value) else int(value)


def _safe_float(value, default=0.0):
    return default if pd.isna(value) else float(value)


def _full_height(df):
    return int(35.2 * (len(df) + 1)) + 3


def _latest_month_column(readings_df):
    if readings_df.empty or len(readings_df.columns) == 0:
        return None
    cols_sorted = sorted(readings_df.columns, key=lambda c: datetime.strptime(c, "%B %Y"))
    return cols_sorted[-1]


def _readings_column(this_month_dict, water_bill, water_units, bill2, bill3):
    col = {f"R{r} Reading": this_month_dict[r] for r in ROOMS}
    col["WaterBill"] = water_bill
    col["WaterBillUnits"] = water_units
    col["ElectricityBill-2"] = bill2
    col["ElectricityBill-3"] = bill3
    return col


def _validate_and_load(uploaded_file):
    try:
        readings_df = pd.read_excel(uploaded_file, sheet_name=READINGS_SHEET, index_col=0)
        paid_df = pd.read_excel(uploaded_file, sheet_name=AMOUNDPAID_SHEET, index_col=0)
        people_df = pd.read_excel(uploaded_file, sheet_name=PEOPLE_SHEET, index_col=0)
    except ValueError as e:
        return False, [f"Could not read the required sheets: {e}"], None

    errors = []
    if list(readings_df.index) != READINGS_ROWS:
        errors.append("'Readings' sheet row labels don't match the expected format.")
    if list(paid_df.index) != AMOUNDPAID_ROWS:
        errors.append("'AmoundPaid' sheet row labels don't match the expected format.")
    if list(people_df.index) != PEOPLE_ROWS:
        errors.append("'NumberOfPeople' sheet row labels don't match the expected format.")
    if PEOPLE_COLUMN not in people_df.columns:
        errors.append(f"'NumberOfPeople' sheet must have a column named '{PEOPLE_COLUMN}'.")

    if errors:
        return False, errors, None
    return True, [], (readings_df, paid_df, people_df)


def _to_xlsx_bytes(readings_df, paid_df, people_df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        readings_df.to_excel(writer, sheet_name=READINGS_SHEET)
        paid_df.to_excel(writer, sheet_name=AMOUNDPAID_SHEET)
        people_df.to_excel(writer, sheet_name=PEOPLE_SHEET)
    return buf.getvalue()


def _calculate_bill(previousMonth, thisMonth, numberOfPeopleAsPerHouse, waterBill, bill2, bill3,
                     unitsConsumedInWaterBill, base_cap, base_rate):
    """Returns (full_df, result_df, totalBill, totalWaterUnits, amounts_dict, error, steps)."""
    bad_rooms = [r for r in ROOMS if thisMonth[r] < previousMonth[r]]
    if bad_rooms:
        return None, None, None, None, None, (
            f"Room(s) {bad_rooms}: 'This Month Units' is less than "
            "'Previous Month Units'. Please check the readings."
        ), None

    totalBill = waterBill + bill2 + bill3

    elec_units_in_water_bill = sum(
        thisMonth[r] - previousMonth[r] for r in ELECTRICITY_ROOMS_IN_WATER_BILL
    )
    totalWaterUnits = unitsConsumedInWaterBill - elec_units_in_water_bill

    data = []
    for room in ROOMS:
        previous_units = previousMonth[room]
        this_units = thisMonth[room]
        data.append(
            {
                "Room Number": room,
                "Number of People": numberOfPeopleAsPerHouse[room],
                "Previous Month Units": previous_units,
                "This Month Units": this_units,
                "Units Consumed": this_units - previous_units,
            }
        )

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
    df["Final Price"] = np.floor(df["Total Bill"]).astype(int)

    result_df = df[["Room Number", "Final Price"]].copy()
    amounts_dict = dict(zip(result_df["Room Number"], result_df["Final Price"]))

    steps = {
        "elec_units_in_water_bill": elec_units_in_water_bill,
        "water_base_units": water_row["Base Units"],
        "water_extra_units": water_row["Extra Units"],
        "water_base_price": water_row["Base Price"],
        "water_extra_price": water_row["Extra Price"],
        "totalWaterBill": totalWaterBill,
        "extraBill": extraBill,
        "totalExtraUnits": totalExtraUnits,
        "amountPerExtraUnit": amountPerExtraUnit,
        "total_people": total_people,
        "waterBillPerPerson": waterBillPerPerson,
        "rooms_base_price_sum": df["Base Price"].sum(),
        "rooms_extra_units_sum": df["Extra Units"].sum(),
    }

    return df, result_df, totalBill, totalWaterUnits, amounts_dict, None, steps


# ---------------------------------------------------------------------------
# SESSION STATE INIT
# ---------------------------------------------------------------------------
if "readings_df" not in st.session_state:
    st.session_state.readings_df = pd.DataFrame(index=READINGS_ROWS)
if "paid_df" not in st.session_state:
    st.session_state.paid_df = pd.DataFrame(index=AMOUNDPAID_ROWS)
if "people_df" not in st.session_state:
    st.session_state.people_df = pd.DataFrame(index=PEOPLE_ROWS)
if "result_df" not in st.session_state:
    st.session_state.result_df = None
if "active_section" not in st.session_state:
    st.session_state.active_section = SECTIONS[0]
if "_nav_request" in st.session_state:
    st.session_state.active_section = st.session_state.pop("_nav_request")
if "calc_inputs" not in st.session_state:
    st.session_state.calc_inputs = {
        "previous": {}, "this": {}, "people": {},
        "waterBill": None, "bill2": None, "bill3": None,
        "unitsConsumedInWaterBill": None, "base_cap": 100, "base_rate": 5.0,
    }

# ---------------------------------------------------------------------------
# HEADER + NAVIGATION
# ---------------------------------------------------------------------------
st.title("🏠 Rental Utility Bill Splitter")
st.caption(
    "Enter meter readings, occupancy, and bill amounts below. "
    "The app calculates each room's final share automatically."
)

st.radio(
    "Navigate", SECTIONS, key="active_section", horizontal=True, label_visibility="collapsed"
)
section = st.session_state.active_section

# ---------------------------------------------------------------------------
# SECTION: CALCULATE
# ---------------------------------------------------------------------------
if section == SECTIONS[0]:
    st.subheader("📤 Upload Existing Data (optional)")
    st.caption(
        "Upload a previously downloaded .xlsx to pre-fill Previous Month readings "
        "and Number of People automatically."
    )
    uploaded_file = st.file_uploader("Upload your saved .xlsx", type=["xlsx"], key="calc_upload")

    if uploaded_file is not None:
        upload_sig = (uploaded_file.name, uploaded_file.size)
        if st.session_state.get("_last_upload_sig") != upload_sig:
            ok, errors, dfs = _validate_and_load(uploaded_file)
            if not ok:
                for err in errors:
                    st.error(err)
            else:
                readings_df, paid_df, people_df = dfs
                st.session_state.readings_df = readings_df
                st.session_state.paid_df = paid_df
                st.session_state.people_df = people_df
                st.session_state._last_upload_sig = upload_sig

                latest_col = _latest_month_column(readings_df)
                if latest_col is not None:
                    for room in ROOMS:
                        val = _safe_int(readings_df.loc[f"R{room} Reading", latest_col])
                        if val is not None:
                            st.session_state[f"prev_{room}"] = val

                if PEOPLE_COLUMN in people_df.columns:
                    for room in ROOMS:
                        val = _safe_int(people_df.loc[f"R{room} People", PEOPLE_COLUMN])
                        if val is not None:
                            st.session_state[f"people_{room}"] = val

                st.success("Uploaded data loaded — Previous Month & Number of People pre-filled below.")

    st.divider()
    st.subheader("1. Room Meter Readings & Occupancy")

    previousMonth = {}
    thisMonth = {}
    numberOfPeopleAsPerHouse = {}

    ci = st.session_state.calc_inputs

    st.markdown("#### 1.1 Previous Month")
    for room in ROOMS:
        previousMonth[room] = st.number_input(
            f"Room {room} (Previous Month)",
            min_value=0, value=ci["previous"].get(room), step=1,
            key=f"prev_{room}",
        )
        ci["previous"][room] = previousMonth[room]

    st.markdown("#### 1.2 This Month")
    for room in ROOMS:
        thisMonth[room] = st.number_input(
            f"Room {room} (This Month)",
            min_value=0, value=ci["this"].get(room), step=1,
            key=f"this_{room}",
        )
        ci["this"][room] = thisMonth[room]

    st.markdown("#### 1.3 Number of People")
    for room in ROOMS:
        numberOfPeopleAsPerHouse[room] = st.number_input(
            f"Room {room} (Number of Occupants)",
            min_value=0, value=ci["people"].get(room), step=1,
            key=f"people_{room}",
        )
        ci["people"][room] = numberOfPeopleAsPerHouse[room]

    st.subheader("2. Utility Bills")
    waterBill = st.number_input(
        "Water Bill (Rs.)",
        min_value=0.0, value=ci["waterBill"], step=1.0,
        help="Includes electricity for rooms 11, 12, 13 and water for all rooms.",
        key="waterBill_input",
    )
    ci["waterBill"] = waterBill
    bill2 = st.number_input(
        "Electricity Bill 2 (Rs.)", min_value=0.0, value=ci["bill2"], step=1.0, key="bill2_input"
    )
    ci["bill2"] = bill2
    bill3 = st.number_input(
        "Electricity Bill 3 (Rs.)", min_value=0.0, value=ci["bill3"], step=1.0, key="bill3_input"
    )
    ci["bill3"] = bill3

    unitsConsumedInWaterBill = st.number_input(
        "Units Consumed shown on Water Bill",
        min_value=0.0, value=ci["unitsConsumedInWaterBill"], step=1.0,
        help="Combined meter units (electricity for 11/12/13 + water for all rooms).",
        key="unitsConsumedInWaterBill_input",
    )
    ci["unitsConsumedInWaterBill"] = unitsConsumedInWaterBill

    st.subheader("3. Pricing Rules")
    base_cap = st.number_input(
        "Base unit cap", min_value=0, value=ci["base_cap"], step=1, key="base_cap_input"
    )
    ci["base_cap"] = base_cap
    base_rate = st.number_input(
        "Base rate per unit (Rs.)", min_value=0.0, value=ci["base_rate"], step=0.5, key="base_rate_input"
    )
    ci["base_rate"] = base_rate

    submitted = st.button("Calculate", use_container_width=True)

    if submitted:
        missing_fields = []

        for room in ROOMS:
            if previousMonth[room] is None:
                missing_fields.append(f"Room {room} - Previous Month Units")
            if thisMonth[room] is None:
                missing_fields.append(f"Room {room} - This Month Units")
            if numberOfPeopleAsPerHouse[room] is None:
                missing_fields.append(f"Room {room} - Number of Occupants")

        if waterBill is None:
            missing_fields.append("Water Bill")
        if bill2 is None:
            missing_fields.append("Electricity Bill 2")
        if bill3 is None:
            missing_fields.append("Electricity Bill 3")
        if unitsConsumedInWaterBill is None:
            missing_fields.append("Units Consumed shown on Water Bill")

        if missing_fields:
            st.error("Please fill in all required fields before calculating.")
            st.warning(
                "Missing fields:\n\n" +
                "\n".join(f"- {field}" for field in missing_fields)
            )
            st.stop()

        try:
            full_df, result_df, totalBill, totalWaterUnits, amounts_dict, error, steps = _calculate_bill(
                previousMonth, thisMonth, numberOfPeopleAsPerHouse, waterBill, bill2, bill3,
                unitsConsumedInWaterBill, base_cap, base_rate,
            )
            if error:
                st.error(error)
            else:
                st.session_state.full_df = full_df
                st.session_state.result_df = result_df
                st.session_state.totalBill = totalBill
                st.session_state.totalWaterUnits = totalWaterUnits
                st.session_state.previousMonth = previousMonth
                st.session_state.thisMonth = thisMonth
                st.session_state.numberOfPeopleAsPerHouse = numberOfPeopleAsPerHouse
                st.session_state.waterBill = waterBill
                st.session_state.bill2 = bill2
                st.session_state.bill3 = bill3
                st.session_state.unitsConsumedInWaterBill = unitsConsumedInWaterBill
                st.session_state.base_cap = base_cap
                st.session_state.base_rate = base_rate
                st.session_state.amounts_dict = amounts_dict
                st.session_state.calc_steps = steps
        except Exception as e:
            st.error(f"Something went wrong in the calculation: {e}")
            st.session_state.result_df = None

    if st.session_state.result_df is not None:
        st.divider()
        st.subheader(f"Utility Bill - {datetime.now().strftime('%B %Y')}")

        result_display = st.session_state.result_df.rename(
            columns={"Final Price": "Amount Due (Rs.)"}
        ).set_index("Room Number")

        st.table(result_display)

        total_collected = int(st.session_state.result_df["Final Price"].sum())
        rounding_shortfall = round(st.session_state.totalBill - total_collected, 2)
        c1, c2 = st.columns(2)
        c1.metric("Total Bill (actual)", f"Rs. {st.session_state.totalBill:,.2f}")
        c2.metric("Total Collected (after rounding down)", f"Rs. {total_collected:,}")
        st.caption(f"Rounding shortfall (not collected): Rs. {rounding_shortfall:,.2f}")

        st.divider()
        if st.button("📊 View / Download Data", use_container_width=True):
            st.session_state._nav_request = SECTIONS[1]
            st.rerun()
        if st.button("🔢 See Full Calculation", use_container_width=True):
            st.session_state._nav_request = SECTIONS[3]
            st.rerun()

# ---------------------------------------------------------------------------
# SECTION: VIEW DATA
# ---------------------------------------------------------------------------
elif section == SECTIONS[1]:
    st.subheader("📊 View Data")

    if st.session_state.result_df is None:
        st.info("Run a calculation on the Calculate tab first.")
    else:
        month_label = datetime.now().strftime("%B %Y")
        st.caption(f"Preview includes the pending {month_label} entry (not yet saved) below.")

        preview_readings = st.session_state.readings_df.copy()
        preview_readings[month_label] = pd.Series(
            _readings_column(
                st.session_state.thisMonth, st.session_state.waterBill,
                st.session_state.unitsConsumedInWaterBill, st.session_state.bill2, st.session_state.bill3,
            )
        )

        preview_paid = st.session_state.paid_df.copy()
        preview_paid[month_label] = pd.Series(
            {f"R{r} Amount": st.session_state.amounts_dict[r] for r in ROOMS}
        )

        preview_people = st.session_state.people_df.copy()
        preview_people[PEOPLE_COLUMN] = pd.Series(
            {f"R{r} People": st.session_state.numberOfPeopleAsPerHouse[r] for r in ROOMS}
        )

        st.markdown("#### Readings")
        st.dataframe(preview_readings, use_container_width=True, height=_full_height(preview_readings))

        st.markdown("#### AmoundPaid")
        st.dataframe(preview_paid, use_container_width=True, height=_full_height(preview_paid))

        st.markdown("#### Number Of People")
        st.dataframe(preview_people, use_container_width=True, height=_full_height(preview_people))

        def _commit_pending():
            st.session_state.readings_df = preview_readings
            st.session_state.paid_df = preview_paid
            st.session_state.people_df = preview_people

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "🔄 Update Data and Download",
                data=_to_xlsx_bytes(preview_readings, preview_paid, preview_people),
                file_name=f"{month_label.replace(' ', '-')}-UtilityBill.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                on_click=_commit_pending,
                use_container_width=True,
            )
        with c2:
            if st.button("✏️ Edit", use_container_width=True):
                st.session_state._nav_request = SECTIONS[2]
                st.rerun()

# ---------------------------------------------------------------------------
# SECTION: EDIT DATA
# ---------------------------------------------------------------------------
elif section == SECTIONS[2]:
    st.subheader("✏️ Edit Pending Data")

    if st.session_state.result_df is None:
        st.info("Run a calculation on the Calculate tab first.")
    else:
        month_label = datetime.now().strftime("%B %Y")
        st.caption(f"Editing the not-yet-saved {month_label} entry. Previous Month figures come from history and aren't edited here.")

        edited_this_month = {}
        for room in ROOMS:
            edited_this_month[room] = st.number_input(
                f"Edit - Room {room} Reading",
                min_value=0, value=int(st.session_state.thisMonth[room]), step=1,
                key=f"edit2_reading_{room}",
            )

        edited_water_bill = st.number_input(
            "Water Bill (Rs.)", min_value=0.0, value=float(st.session_state.waterBill), step=1.0,
            key="edit2_water_bill",
        )
        edited_water_units = st.number_input(
            "Water Bill Units", min_value=0.0, value=float(st.session_state.unitsConsumedInWaterBill), step=1.0,
            key="edit2_water_units",
        )
        edited_bill2 = st.number_input(
            "Electricity Bill 2 (Rs.)", min_value=0.0, value=float(st.session_state.bill2), step=1.0,
            key="edit2_bill2",
        )
        edited_bill3 = st.number_input(
            "Electricity Bill 3 (Rs.)", min_value=0.0, value=float(st.session_state.bill3), step=1.0,
            key="edit2_bill3",
        )

        st.divider()
        edited_people = {}
        for room in ROOMS:
            edited_people[room] = st.number_input(
                f"Edit - Room {room} - Number of People",
                min_value=0, value=int(st.session_state.numberOfPeopleAsPerHouse[room]), step=1,
                key=f"edit2_people_{room}",
            )

        st.divider()
        if st.button("💾 Save - Recalculate - Back to View", use_container_width=True):
            full_df, result_df, totalBill, totalWaterUnits, amounts_dict, error, steps = _calculate_bill(
                st.session_state.previousMonth, edited_this_month, edited_people,
                edited_water_bill, edited_bill2, edited_bill3, edited_water_units,
                st.session_state.base_cap, st.session_state.base_rate,
            )
            if error:
                st.error(error)
            else:
                st.session_state.thisMonth = edited_this_month
                st.session_state.numberOfPeopleAsPerHouse = edited_people
                st.session_state.waterBill = edited_water_bill
                st.session_state.unitsConsumedInWaterBill = edited_water_units
                st.session_state.bill2 = edited_bill2
                st.session_state.bill3 = edited_bill3
                st.session_state.full_df = full_df
                st.session_state.result_df = result_df
                st.session_state.totalBill = totalBill
                st.session_state.totalWaterUnits = totalWaterUnits
                st.session_state.amounts_dict = amounts_dict
                st.session_state.calc_steps = steps
                st.session_state._nav_request = SECTIONS[1]
                st.rerun()

# ---------------------------------------------------------------------------
# SECTION: FULL CALCULATION
# ---------------------------------------------------------------------------
else:
    st.subheader("🔢 Full Calculation")

    if st.session_state.result_df is None:
        st.info("Run a calculation on the Calculate tab first.")
    else:
        s = st.session_state.calc_steps

        st.markdown("#### Detailed Breakdown (per room)")
        st.dataframe(
            st.session_state.full_df, use_container_width=True, height=_full_height(st.session_state.full_df)
        )

        st.divider()
        st.markdown("#### The Maths, Step by Step")

        st.markdown("**Step 1 — Total bill received**")
        st.markdown(
            f"`totalBill = waterBill + bill2 + bill3 = "
            f"{st.session_state.waterBill:,.2f} + {st.session_state.bill2:,.2f} + {st.session_state.bill3:,.2f} "
            f"= Rs. {st.session_state.totalBill:,.2f}`"
        )

        st.markdown("**Step 2 — Isolate the water-only units from the combined meter**")
        elec_terms = " + ".join(
            f"({st.session_state.thisMonth[r]} - {st.session_state.previousMonth[r]})"
            for r in ELECTRICITY_ROOMS_IN_WATER_BILL
        )
        st.markdown(
            f"`elecUnitsInWaterBill (R11+R12+R13) = {elec_terms} = {s['elec_units_in_water_bill']:,.0f} units`"
        )
        st.markdown(
            f"`totalWaterUnits = unitsConsumedInWaterBill - elecUnitsInWaterBill = "
            f"{st.session_state.unitsConsumedInWaterBill:,.0f} - {s['elec_units_in_water_bill']:,.0f} "
            f"= {st.session_state.totalWaterUnits:,.0f} units`"
        )

        st.markdown("**Step 3 — Base + Extra pricing, shared across all 13 rooms and the Water pool**")
        st.markdown(
            f"Each room and the Water pool get up to **{st.session_state.base_cap} units** at the flat "
            f"**Rs. {st.session_state.base_rate}/unit** base rate; anything beyond that is 'Extra'."
        )
        combined_base = s["rooms_base_price_sum"] + s["water_base_price"]
        st.markdown(
            f"`sum of everyone's Base Price = rooms' Rs. {s['rooms_base_price_sum']:,.2f} + "
            f"Water's Rs. {s['water_base_price']:,.2f} = Rs. {combined_base:,.2f}`"
        )
        st.markdown(
            f"`extraBill = totalBill - sum of everyone's Base Price = "
            f"{st.session_state.totalBill:,.2f} - {combined_base:,.2f} = Rs. {s['extraBill']:,.2f}`"
        )
        st.markdown(
            f"`totalExtraUnits = rooms' {s['rooms_extra_units_sum']:,.0f} + Water's {s['water_extra_units']:,.0f} "
            f"= {s['totalExtraUnits']:,.0f} units`"
        )
        st.markdown(
            f"`amountPerExtraUnit = extraBill / totalExtraUnits = "
            f"{s['extraBill']:,.2f} / {s['totalExtraUnits']:,.0f} = Rs. {s['amountPerExtraUnit']:,.4f} per unit` "
            "— this single rate applies to every room's and the Water pool's Extra Units."
        )

        st.markdown("**Step 4 — The Water pool's own total bill**")
        st.markdown(
            f"`Water Base Price = min({st.session_state.base_cap}, {st.session_state.totalWaterUnits:,.0f}) x "
            f"{st.session_state.base_rate} = {s['water_base_units']:,.0f} x {st.session_state.base_rate} "
            f"= Rs. {s['water_base_price']:,.2f}`"
        )
        st.markdown(
            f"`Water Extra Price = {s['water_extra_units']:,.0f} x {s['amountPerExtraUnit']:,.4f} "
            f"= Rs. {s['water_extra_price']:,.2f}`"
        )
        st.markdown(
            f"`totalWaterBill = Water Base Price + Water Extra Price = "
            f"{s['water_base_price']:,.2f} + {s['water_extra_price']:,.2f} = Rs. {s['totalWaterBill']:,.2f}`"
        )

        st.markdown("**Step 5 — Redistribute the Water pool's total by headcount, not usage**")
        st.markdown(
            f"`waterBillPerPerson = totalWaterBill / total_people = "
            f"{s['totalWaterBill']:,.2f} / {s['total_people']:,.0f} = Rs. {s['waterBillPerPerson']:,.4f} per person`"
        )

        st.markdown("**Step 6 — Each room's final bill**")
        st.markdown(
            "`Total Bill (room) = Base Price + Extra Price + (waterBillPerPerson x room's occupants)`  \n"
            "`Final Price = floor(Total Bill)` — rounded down to the nearest rupee "
            "(see the table above for every room's actual numbers)."
        )
