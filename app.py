"""
Formuesimulator — Interaktiv porteføljeberegner med inflasjonsjustering
Beregner netto realverdi for fire aktivaklasser over 15 år.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date


# ─────────────────────────────────────────────────────────────────────────────
# HJELPEFUNKSJONER
# ─────────────────────────────────────────────────────────────────────────────

def monthly_rate(annual_pct: float) -> float:
    """Konverter årlig rente (%) til ekvivalent månedlig rente."""
    return (1 + annual_pct / 100) ** (1 / 12) - 1


def nok(value: float) -> str:
    """Formater tall som norske kroner med mellomrom som tusenseparator."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        s = f"{v / 1_000_000:.2f}".replace(".", ",")
        return f"{sign}{s} mill. kr"
    s = f"{int(round(v)):,}".replace(",", " ")
    return f"{sign}{s} kr"


# ─────────────────────────────────────────────────────────────────────────────
# PORTEFØLJEBEREGNING
# ─────────────────────────────────────────────────────────────────────────────

SKATT_UTLEIE = 0.22
SKATT_UTBYTTE = 0.3784


# ─────────────────────────────────────────────────────────────────────────────
# BREAK-EVEN ANALYSE
# ─────────────────────────────────────────────────────────────────────────────

def _fund_scenario_params(p: dict) -> dict:
    """
    Returner modifiserte parametere for 'Fond-scenarioet':
      - Selg begge eiendommer i dag og invester egenkapitalen i fondet
      - Omdiriger den manedsstrommen som tidligere gikk til renter og
        avdrag (minus tapt leieinntekt etter skatt) til ekstra fondsparing
      - Alle eiendomsrelaterte verdier nullstilles
    """
    p2 = dict(p)

    def _net_cf(val, loan, rate, repay, fees, rental):
        """Netto CF for en eiendom ved gitte parametere."""
        interest = loan * rate / 100 / 12
        taxable  = rental * 0.90 - fees - interest
        after_tax = taxable * (1 - SKATT_UTLEIE) if taxable > 0 else taxable
        return after_tax - repay

    # Egenkapital i dag
    equity1 = max(p["property_value"] - p["property_loan"], 0.0)
    equity2 = max(p["p2_value"]       - p["p2_loan"],       0.0)

    # Manedlig kontantstrom ved gjeldende rente (bolig 1 og 2)
    cf1 = _net_cf(p["property_value"], p["property_loan"], p["loan_rate"],
                  p["monthly_repayment"], p["property_fees"], p["rental_income"])
    cf2 = _net_cf(p["p2_value"], p["p2_loan"], p["p2_loan_rate"],
                  p["p2_repayment"], p["p2_fees"], p["p2_rental"])

    # Frigjort manedsliquiditet ved salg:
    #   du sparer avdrag + renter, men mister leieinntekt (etter skatt)
    int1    = p["property_loan"] * p["loan_rate"] / 100 / 12
    int2    = p["p2_loan"]       * p["p2_loan_rate"] / 100 / 12
    freed1  = p["monthly_repayment"] + int1 - (cf1 + p["monthly_repayment"])  # = int1 - after_tax_rental1
    freed2  = p["p2_repayment"]      + int2 - (cf2 + p["p2_repayment"])

    p2["fund_capital"] = p["fund_capital"] + equity1 + equity2
    p2["fund_monthly"] = p["fund_monthly"] + freed1 + freed2

    # Nullstill eiendom
    for key in ["property_value", "property_loan", "rental_income", "monthly_repayment",
                "p2_value", "p2_loan", "p2_rental", "p2_repayment"]:
        p2[key] = 0

    return p2


def calculate_breakeven_rate(p: dict) -> float | None:
    """
    Finn renten der 'Eiendom-strategien' og 'Fond-strategien' gir
    identisk total realformue etter 15 ar.

    - Fond-scenarioet er FAST (basert pa salg til dagens parametere).
    - Eiendom-scenarioet kjoeres for hvert rentepunkt 2.0 % -> 10.0 %.
    - Returnerer interpolert break-even-rente, eller None hvis ingen krysning.
    """
    fund_params = _fund_scenario_params(p)
    fund_df, _, _ = calculate_portfolio(fund_params)
    fund_total    = fund_df["real_total"].iloc[-1]

    rates     = [round(i * 0.1, 1) for i in range(20, 101)]  # 2.0 ... 10.0
    prev_diff = None
    prev_rate = None

    for rate in rates:
        p_test        = {**p, "loan_rate": rate}
        prop_df, _, _ = calculate_portfolio(p_test)
        prop_total = prop_df["real_total"].iloc[-1]
        diff       = prop_total - fund_total   # positiv = eiendom bedre

        if prev_diff is not None and prev_diff * diff < 0:
            # Lineaer interpolasjon for noeyaktig krysningspunkt
            t = abs(prev_diff) / (abs(prev_diff) + abs(diff))
            return round(prev_rate + t * (rate - prev_rate), 2)

        prev_diff = diff
        prev_rate = rate

    return None   # ingen krysning funnet i 2–10 % intervallet


def calculate_portfolio(p: dict) -> tuple:
    """
    Manedlig simulering over 15 ar.

    Nye features:
    - use_ask:          utbytte reinvesteres skattefritt; exit-skatt betales ar 15
    - apply_wealth_tax: formuesskatt 1,1 % pa nettoformue > 1,7 mill. trekkes arlig
    - usd_nok_change:   justerer fondets effektive avkastning for valutaeksponering
    """
    MONTHS = 15 * 12

    effective_growth  = p["property_growth"] - (p["loan_rate"] - 5.0) * 0.5
    effective_growth2 = p["p2_growth"]       - (p["p2_loan_rate"] - 5.0) * 0.5

    # Feature 7: valutajustering pavirker fondets effektive avkastning
    eff_fund_return = p["fund_return"] + p.get("usd_nok_change", 0.0)

    r_prop   = monthly_rate(effective_growth)
    r_prop2  = monthly_rate(effective_growth2)
    r_fund   = monthly_rate(eff_fund_return)
    r_stocks = monthly_rate(p["stocks_return"])
    r_alt    = monthly_rate(p["alt_growth"])
    r_inf    = monthly_rate(p["inflation"])

    start_ts = pd.Timestamp(date.today().replace(day=1))
    dates    = [start_ts + pd.DateOffset(months=m) for m in range(MONTHS + 1)]

    prop_val        = float(p["property_value"])
    prop_loan       = float(p["property_loan"])
    prop2_val       = float(p["p2_value"])
    prop2_loan      = float(p["p2_loan"])
    fund_val        = float(p["fund_capital"])
    stocks_val      = float(p["stocks_capital"])
    alt_val         = float(p["alt_capital"])
    # CF delt i to sub-kontoer for ASK-separering
    cf_rental_val   = 0.0
    cf_dividend_val = 0.0
    ask_cost_basis  = 0.0   # sum av bruttouvbytte lagt inn (kostpris ved exit)
    total_wt        = 0.0   # akkumulert formuesskatt betalt

    rows = []

    for m in range(MONTHS + 1):
        inflation_factor = (1 + r_inf) ** m
        kpi_factor       = (1 + p["inflation"] / 100) ** (m // 12)

        # ── Utleiebolig 1 ──────────────────────────────────────────────────
        current_rental = p["rental_income"] * kpi_factor
        int1           = prop_loan * p["loan_rate"] / 100 / 12
        taxable1       = current_rental * 0.90 - p["property_fees"] - int1
        after_tax1     = taxable1 * (1 - SKATT_UTLEIE) if taxable1 > 0 else taxable1
        prop_net_cf    = after_tax1 - p["monthly_repayment"]

        # ── Utleiebolig 2 ──────────────────────────────────────────────────
        current_rental2 = p["p2_rental"] * kpi_factor
        int2            = prop2_loan * p["p2_loan_rate"] / 100 / 12
        taxable2        = current_rental2 * 0.90 - p["p2_fees"] - int2
        after_tax2      = taxable2 * (1 - SKATT_UTLEIE) if taxable2 > 0 else taxable2
        prop2_net_cf    = after_tax2 - p["p2_repayment"]

        # ── Feature 2: Utbytte — ASK vs. vanlig ───────────────────────────
        gross_div      = 0.0
        dividend_to_cf = 0.0
        if m > 0 and m % 12 == 0:
            gross_div = stocks_val * p["stocks_dividend"] / 100
            if p.get("use_ask", False):
                dividend_to_cf  = gross_div              # ingen arlig skatt
                ask_cost_basis += gross_div              # spor kostpris
            else:
                dividend_to_cf = gross_div * (1 - SKATT_UTBYTTE)

        # ── Oppdater aktivaverdier ─────────────────────────────────────────
        if m > 0:
            prop_loan       = max(prop_loan  - p["monthly_repayment"], 0.0)
            prop2_loan      = max(prop2_loan - p["p2_repayment"],       0.0)
            prop_val        = prop_val  * (1 + r_prop)
            prop2_val       = prop2_val * (1 + r_prop2)
            stocks_val      = stocks_val * (1 + r_stocks) + p["stocks_monthly"]
            alt_val         = max(alt_val * (1 + r_alt) - p["alt_costs"], 0.0)
            fund_val        = fund_val * (1 + r_fund) + p["fund_monthly"]
            cf_rental_val   = cf_rental_val   * (1 + r_fund) + max(prop_net_cf, 0.0) + max(prop2_net_cf, 0.0)
            cf_dividend_val = cf_dividend_val * (1 + r_fund) + dividend_to_cf

        prop_equity  = prop_val  - prop_loan
        prop2_equity = prop2_val - prop2_loan
        cf_val       = cf_rental_val + cf_dividend_val

        # ── Feature 2: ASK exit-skatt ved siste maned ─────────────────────
        if m == MONTHS and p.get("use_ask", False) and cf_dividend_val > 0:
            ask_gain        = max(cf_dividend_val - ask_cost_basis, 0.0)
            ask_exit_tax    = ask_gain * SKATT_UTBYTTE
            cf_dividend_val = max(cf_dividend_val - ask_exit_tax, 0.0)
            cf_val          = cf_rental_val + cf_dividend_val

        # ── Feature 4: Formuesskatt arlig ─────────────────────────────────
        if p.get("apply_wealth_tax", False) and m > 0 and m % 12 == 0:
            nettoformue = prop_equity + prop2_equity + fund_val + stocks_val + alt_val + cf_val
            wt          = max(nettoformue - 1_700_000, 0.0) * 0.011
            total_wt   += wt
            # Trekk fra CF forst, deretter fond
            wt_from_cf  = min(wt, cf_val)
            cf_val     -= wt_from_cf
            if cf_rental_val + cf_dividend_val > 0:
                ratio           = cf_val / (cf_rental_val + cf_dividend_val)
                cf_rental_val  *= ratio
                cf_dividend_val *= ratio
            else:
                cf_rental_val = cf_dividend_val = 0.0
            fund_val = max(fund_val - (wt - wt_from_cf), 0.0)

        # ── Realverdier ────────────────────────────────────────────────────
        real_prop   = prop_equity  / inflation_factor
        real_prop2  = prop2_equity / inflation_factor
        real_fund   = fund_val     / inflation_factor
        real_stocks = stocks_val   / inflation_factor
        real_alt    = alt_val      / inflation_factor
        real_cf     = cf_val       / inflation_factor
        real_total  = real_prop + real_prop2 + real_fund + real_stocks + real_alt + real_cf

        rows.append({
            "date":             dates[m],
            "month":            m,
            "prop_val":         prop_val,
            "prop_loan":        prop_loan,
            "prop_equity":      prop_equity,
            "prop2_val":        prop2_val,
            "prop2_loan":       prop2_loan,
            "prop2_equity":     prop2_equity,
            "fund_val":         fund_val,
            "stocks_val":       stocks_val,
            "alt_val":          alt_val,
            "cf_val":           cf_val,
            "nominal_total":    prop_equity + prop2_equity + fund_val + stocks_val + alt_val + cf_val,
            "real_prop":        real_prop,
            "real_prop2":       real_prop2,
            "real_fund":        real_fund,
            "real_stocks":      real_stocks,
            "real_alt":         real_alt,
            "real_cf":          real_cf,
            "real_total":       real_total,
            "current_rental":   current_rental,
            "monthly_interest": int1,
            "prop_net_cf":      prop_net_cf,
            "prop2_net_cf":     prop2_net_cf,
            "dividend_net":     gross_div,
            "wealth_tax_accum": total_wt,
        })

    return pd.DataFrame(rows), effective_growth, total_wt


# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT-APP
# ─────────────────────────────────────────────────────────────────────────────

def build_sidebar() -> dict:
    """Bygg sidebar og returner alle parametre som en dict."""
    st.sidebar.markdown("## ⚙️ Parametere")

    with st.sidebar.expander("🏠 Eiendom (Sekundærbolig)", expanded=True):
        property_value    = st.number_input("Markedsverdi (kr)",            value=4_750_000, step=50_000,  min_value=0,   key="pv")
        property_loan     = st.number_input("Lån (kr)",                     value=3_700_000, step=50_000,  min_value=0,   key="pl")
        loan_rate         = st.number_input("Lånerente (% p.a.)",           value=5.34,      step=0.01,    min_value=0.0, format="%.2f", key="lr")
        monthly_repayment = st.number_input("Månedlig avdrag (kr/mnd)",     value=10_000,    step=500,     min_value=0,   key="mr")
        property_fees     = st.number_input("Felleskostnader (kr/mnd)",     value=2_360,     step=100,     min_value=0,   key="pf")
        rental_income     = st.number_input("Leieinntekt (kr/mnd)",         value=18_000,    step=500,     min_value=0,   key="ri")
        property_growth   = st.slider("Prisvekst eiendom (% p.a.)", min_value=0.0, max_value=10.0, value=5.6, step=0.1, format="%.1f", key="pg")

    with st.sidebar.expander("🏠 Utleiebolig 2", expanded=False):
        include_prop2 = st.checkbox("Aktiver utleiebolig 2", value=False, key="inc2")
        if include_prop2:
            p2_value     = st.number_input("Markedsverdi (kr)",           value=2_500_000, step=50_000,  min_value=0,   key="p2v")
            p2_loan      = st.number_input("Lån (kr)",                    value=2_000_000, step=50_000,  min_value=0,   key="p2l")
            p2_loan_rate = st.number_input("Lånerente (% p.a.)",          value=5.34,      step=0.01,    min_value=0.0, format="%.2f", key="p2lr")
            p2_repayment = st.number_input("Månedlig avdrag (kr/mnd)",    value=7_000,     step=500,     min_value=0,   key="p2mr")
            p2_fees      = st.number_input("Felleskostnader (kr/mnd)",    value=2_000,     step=100,     min_value=0,   key="p2pf")
            p2_rental    = st.number_input("Leieinntekt (kr/mnd)",        value=12_000,    step=500,     min_value=0,   key="p2ri")
            p2_growth    = st.slider("Prisvekst eiendom (% p.a.)", min_value=0.0, max_value=10.0, value=5.6, step=0.1, format="%.1f", key="p2pg")
        else:
            p2_value = p2_loan = p2_repayment = p2_fees = p2_rental = 0
            p2_loan_rate = p2_growth = 0.0

    with st.sidebar.expander("📈 Aksjefond", expanded=True):
        fund_capital = st.number_input("Startkapital (kr)",     value=1_800_000, step=50_000, min_value=0, key="fk")
        fund_monthly = st.number_input("Månedlig sparing (kr)", value=15_000,    step=500,   min_value=0, key="fm")
        # Avkastningen er justert for valutaeksponering; vi antar langsiktig
        # nøytral utvikling i USD/NOK (ingen valutagevinst/-tap innbakt).
        fund_return  = st.slider("Avkastning (% p.a.)", min_value=0.0, max_value=15.0, value=3.0, step=0.1, format="%.1f", key="fr")
        use_ask      = st.checkbox("Bruk ASK (skattefri reinvestering, exit-skatt ar 15)", value=False, key="ask")

    with st.sidebar.expander("📊 Enkeltaksjer", expanded=True):
        stocks_capital  = st.number_input("Startkapital (kr)",              value=200_000, step=10_000, min_value=0,   key="sk")
        stocks_monthly  = st.number_input("Månedlig sparing (kr)",          value=0,       step=500,   min_value=0,   key="sm")
        stocks_return   = st.number_input("Avkastning (% p.a.)",            value=10.0,    step=0.1,   format="%.1f", key="sr")
        stocks_dividend = st.number_input("Utbytterate (% av portefølje)",  value=3.0,     step=0.1,   format="%.1f", key="sd")

    with st.sidebar.expander("🎨 Alternative Investeringer", expanded=True):
        alt_capital = st.number_input("Startkapital (kr)",           value=500_000, step=10_000, min_value=0,   key="ak")
        alt_growth  = st.number_input("Verdiøkning (% p.a.)",        value=4.0,     step=0.1,   format="%.1f", key="ag")
        alt_costs   = st.number_input("Løpende kostnader (kr/mnd)",  value=1_000,   step=100,   min_value=0,   key="ac")

    with st.sidebar.expander("🌍 Makro", expanded=True):
        inflation        = st.number_input("Inflasjon / KPI (% p.a.)", value=2.1, step=0.1, format="%.1f", key="inf")
        # Positiv verdi = USD styrker seg mot NOK => fondets NOK-avkastning oker
        usd_nok_change   = st.slider("USD/NOK arlig endring (pp)", min_value=-5.0, max_value=5.0, value=0.0, step=0.1, format="%.1f", key="usdnok")
        apply_wealth_tax = st.checkbox("Beregn formuesskatt (1,1 % over 1,7 mill. kr)", value=False, key="wt")

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "📌 Leieinntekt vokser med KPI arlig. 10 % meglergebyr trekkes for skatteberegning.  \n"
        "📌 Positiv netto CF (etter skatt og avdrag) + utbytte samles i egen CF-posisjon.  \n"
        "📌 Alle lag i grafen er inflasjonsjustert til **dagens kjoepekraft**."
    )

    return dict(
        property_value=property_value,     property_loan=property_loan,
        loan_rate=loan_rate,               monthly_repayment=monthly_repayment,
        property_fees=property_fees,       rental_income=rental_income,
        property_growth=property_growth,
        # Utleiebolig 2
        p2_value=p2_value,       p2_loan=p2_loan,
        p2_loan_rate=p2_loan_rate, p2_repayment=p2_repayment,
        p2_fees=p2_fees,           p2_rental=p2_rental,
        p2_growth=p2_growth,
        # Fond og aksjer
        fund_capital=fund_capital,       fund_monthly=fund_monthly,
        fund_return=fund_return,         use_ask=use_ask,
        stocks_capital=stocks_capital,   stocks_monthly=stocks_monthly,
        stocks_return=stocks_return,     stocks_dividend=stocks_dividend,
        alt_capital=alt_capital,         alt_growth=alt_growth,
        alt_costs=alt_costs,
        # Makro
        inflation=inflation,             usd_nok_change=usd_nok_change,
        apply_wealth_tax=apply_wealth_tax,
    )


def render_chart(df: pd.DataFrame) -> None:
    """Stacked area-chart: fem fargede lag + stiplet totallinje."""
    PALETTE = {
        "real_prop":   "#3B82F6",   # blaa   — utleiebolig 1
        "real_prop2":  "#0EA5E9",   # lysbla — utleiebolig 2
        "real_fund":   "#22C55E",   # gronn
        "real_stocks": "#F59E0B",   # gul
        "real_alt":    "#A855F7",   # lilla
        "real_cf":     "#F43F5E",   # roed/rosa — akkumulert kontantstrom
    }
    LABELS = {
        "real_prop":   "Utleiebolig 1 (egenkapital)",
        "real_prop2":  "Utleiebolig 2 (egenkapital)",
        "real_fund":   "Aksjefond",
        "real_stocks": "Enkeltaksjer",
        "real_alt":    "Alternative investeringer",
        "real_cf":     "Akkumulert kontantstrom",
    }

    fig = go.Figure()

    for col in ["real_prop", "real_prop2", "real_fund", "real_stocks", "real_alt", "real_cf"]:
        fig.add_trace(go.Scatter(
            x=df["date"],
            y=df[col].clip(lower=0),
            name=LABELS[col],
            stackgroup="one",
            mode="lines",
            line=dict(color=PALETTE[col], width=0.6),
            fillcolor=PALETTE[col],
            opacity=0.82,
            hovertemplate=(
                f"<b>{LABELS[col]}</b><br>"
                "%{x|%B %Y}<br>"
                "%{y:,.0f} kr"
                "<extra></extra>"
            ),
        ))

    # Stiplet totallinje
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["real_total"],
        name="Total realverdi",
        mode="lines",
        line=dict(color="#F8FAFC", width=1.8, dash="dot"),
        hovertemplate="<b>Total</b><br>%{x|%B %Y}<br>%{y:,.0f} kr<extra></extra>",
    ))

    fig.update_layout(
        height=500,
        xaxis=dict(
            title="",
            gridcolor="#1e2535",
            tickformat="%Y",
            dtick="M24",
            ticklabelmode="instant",
        ),
        yaxis=dict(
            title="Realverdi — kjoepekraft i dagens kroner",
            gridcolor="#1e2535",
            tickformat=",.0f",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=12),
        ),
        hovermode="x unified",
        plot_bgcolor="#0b0f19",
        paper_bgcolor="#0b0f19",
        font=dict(color="#cbd5e1", size=13),
        margin=dict(l=80, r=20, t=10, b=50),
    )

    st.plotly_chart(fig, width="stretch")


def render_metrics(
    df: pd.DataFrame,
    p: dict,
    effective_growth: float,
    breakeven_rate: float | None,
    total_wt: float = 0.0,
) -> None:
    """Vis nokkeltall, break-even og kontantstrom-forklaring."""
    row0 = df.iloc[0]
    rowN = df.iloc[-1]

    today_net  = row0["nominal_total"]
    final_nom  = rowN["nominal_total"]
    final_real = rowN["real_total"]
    final_loan = rowN["prop_loan"]
    final_cf   = rowN["cf_val"]

    # ASK-fordel: sammenlign med motsatt scenario
    if p.get("use_ask", False):
        _df_no_ask, _, _ = calculate_portfolio({**p, "use_ask": False})
        ask_delta = final_real - _df_no_ask["real_total"].iloc[-1]
        ask_label = "ASK-fordel vs. vanlig fond"
    else:
        _df_ask, _, _ = calculate_portfolio({**p, "use_ask": True})
        ask_delta = _df_ask["real_total"].iloc[-1] - final_real
        ask_label = "Mulig ASK-gevinst"

    interest_now     = row0["monthly_interest"]
    interest_final   = rowN["monthly_interest"]
    interest_savings = (interest_now - interest_final) * 12

    net_cf_now   = row0["prop_net_cf"]
    net_cf_final = rowN["prop_net_cf"]
    rental_now   = row0["current_rental"]
    rental_final = rowN["current_rental"]

    # ── Rad 1: portefoljenokler ────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Nettoformue i dag",   nok(today_net))
    c2.metric("Om 15 ar — nominell", nok(final_nom),  delta=nok(final_nom  - today_net))
    c3.metric("Om 15 ar — realverdi",nok(final_real), delta=nok(final_real - today_net))
    c4.metric("Restgjeld om 15 ar",  nok(final_loan), delta=nok(final_loan - p["property_loan"]))
    c5.metric("Akkum. kontantstrom", nok(final_cf))
    ask_icon = "✅" if p.get("use_ask") else "💡"
    c6.metric(f"{ask_icon} {ask_label}", nok(ask_delta),
              delta="gevinst over 15 ar" if ask_delta >= 0 else "lavere enn ASK")

    # ── Rad 2: break-even ──────────────────────────────────────────────────
    st.markdown("##### Break-even: Eiendom vs. Fond-strategi")
    b1, b2, b3 = st.columns([1, 1, 2])

    current_rate = p["loan_rate"]

    if breakeven_rate is None:
        # Avgjor hvilken strategi som vinner utenfor intervallet
        fund_params    = _fund_scenario_params(p)
        fund_df, _, _  = calculate_portfolio(fund_params)
        fund_total     = fund_df["real_total"].iloc[-1]
        prop_total  = rowN["real_total"]

        prop_wins = prop_total > fund_total
        dot       = "🟢" if prop_wins else "🔴"
        winner    = "Eiendom bedre" if prop_wins else "Fond bedre"

        b1.metric(f"{dot} Break-even rente", "Ingen krysning (2–10 %)")
        b2.metric("Strategi-vinner", winner)
    else:
        diff_pp   = breakeven_rate - current_rate
        in_zone   = current_rate < breakeven_rate   # eiendom er fortsatt lonnsom
        dot       = "🟢" if in_zone else "🔴"
        sign      = "+" if diff_pp >= 0 else ""

        b1.metric(
            f"{dot} Break-even rente",
            f"{breakeven_rate:.2f} %",
            delta=f"{sign}{diff_pp:.2f} pp fra na ({current_rate:.2f} %)",
        )
        if in_zone:
            b2.metric(
                "Status na",
                "Eiendom bedre 🟢",
                delta=f"Fond vinner over {breakeven_rate:.2f} %",
            )
        else:
            b2.metric(
                "Status na",
                "Fond bedre 🔴",
                delta=f"Eiendom vinner under {breakeven_rate:.2f} %",
                delta_color="inverse",
            )

    with b3:
        st.caption(
            "🟢 Grønn = dagens rente er UNDER break-even — eiendomsstrategien er fortsatt lønnsom.  \n"
            "🔴 Rød   = dagens rente er OVER break-even — fond-strategien gir høyere realformue etter 15 år.  \n"
            "Break-even beregnes ved å iterere renter 2–10 % og finne krysningspunktet mellom de to strategiene."
        )

    # ── Info-boks: kontantstrom-detaljer ──────────────────────────────────
    growth_adj = effective_growth - p["property_growth"]
    sign_str   = "- " if growth_adj < 0 else "+ "

    info_lines = [
        f"Effektiv eiendomsvekst: **{effective_growth:.2f} % p.a.** "
        f"({p['property_growth']:.1f} % nominelt {sign_str}{abs(growth_adj):.2f} pp "
        f"rentekorrigering ved {p['loan_rate']:.2f} % rente).",

        f"**Leie i dag:** {nok(rental_now)}/mnd → "
        f"**ar 15:** {nok(rental_final)}/mnd etter KPI-justering. "
        f"10 % meglergebyr trekkes for skatteberegning.",

        f"**Maned 1 — netto CF:** Leie {nok(rental_now * 0.9)} (etter megler) "
        f"- Felleskostnader {nok(p['property_fees'])} "
        f"- Renter {nok(interest_now)} - Avdrag {nok(p['monthly_repayment'])} "
        f"= **{nok(net_cf_now)}/mnd** etter 22 % skatt.",

        f"**Maned 180 — netto CF:** Renter redusert til {nok(interest_final)}/mnd "
        f"(sparer {nok(interest_savings)}/ar). CF: **{nok(net_cf_final)}/mnd**.",
    ]
    st.info("  \n".join(info_lines))


def render_yearly_table(df: pd.DataFrame) -> None:
    """Vis sammendrag per år i en ekspanderbar tabell."""
    with st.expander("📋 Vis detaljert år-for-år-oversikt"):
        yearly = df[df["month"] % 12 == 0].copy()
        yearly["År"] = (yearly["month"] / 12).astype(int)

        tbl = yearly[[
            "År", "prop_loan", "prop2_loan",
            "real_prop", "real_prop2", "real_fund",
            "real_stocks", "real_alt", "real_cf", "real_total", "nominal_total",
        ]].copy()

        tbl.columns = [
            "År", "Restgjeld B1", "Restgjeld B2",
            "Bolig 1 (real)", "Bolig 2 (real)", "Aksjefond (real)", "Enkeltaksjer (real)",
            "Alternativt (real)", "Kontantstrom (real)", "Total realverdi", "Total nominell",
        ]

        for col in tbl.columns[1:]:
            tbl[col] = tbl[col].apply(nok)

        st.dataframe(tbl, width="stretch", hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# HISTORISKE REFERANSEVERDIER
# ─────────────────────────────────────────────────────────────────────────────

HIST_DATA = [
    {"Aktivaklasse": "Oslo Bors (OSEBX)",        "Periode": "1996–2025", "Snitt p.a.": "10,2 %", "Verste ar":    "-54 % (2008)"},
    {"Aktivaklasse": "Norske boligpriser",        "Periode": "2000–2025", "Snitt p.a.":  "6,8 %", "Verste ar":    "-12 % (2022)"},
    {"Aktivaklasse": "MSCI World (NOK-hedget)",   "Periode": "2000–2025", "Snitt p.a.":  "8,9 %", "Verste ar":    "-40 % (2008)"},
    {"Aktivaklasse": "Norsk inflasjon (KPI)",     "Periode": "2000–2025", "Snitt p.a.":  "2,4 %", "Verste ar":    "7,5 % (2022)"},
    {"Aktivaklasse": "Statsobligasjoner 10Y",     "Periode": "2000–2025", "Snitt p.a.":  "3,8 %", "Verste ar":    "-10 % (2022)"},
    {"Aktivaklasse": "Global eiendom (REIT)",     "Periode": "2000–2025", "Snitt p.a.":  "7,1 %", "Verste ar":    "-60 % (2008)"},
]


def render_sensitivity(p: dict) -> None:
    """3x3 scenariogrid: fondavkastning (rader) x boligvekst (kolonner)."""
    with st.expander("🔬 Sensitivitetsanalyse — Total realformue om 15 ar"):
        st.caption(
            "Hver celle viser **total netto realformue** (inflasjonsjustert) ved arets slutt "
            "for ulike kombinasjoner av fondavkastning og boligvekst. "
            "Den midtre cellen (basis) tilsvarer dine na gjeldende innstillinger."
        )

        fund_base = p["fund_return"]
        prop_base = p["property_growth"]
        fund_deltas = [-2.0, 0.0, 2.0]
        prop_deltas = [-2.0, 0.0, 2.0]

        col_labels = [f"Bolig {prop_base + d:.1f} %" for d in prop_deltas]

        rows_data = []
        raw_values = []  # for fargekodig
        for fd in fund_deltas:
            row_vals = []
            for pd_ in prop_deltas:
                p_t = {**p, "fund_return": fund_base + fd, "property_growth": prop_base + pd_}
                df_t, _, _ = calculate_portfolio(p_t)
                row_vals.append(df_t["real_total"].iloc[-1])
            raw_values.append(row_vals)
            rows_data.append({
                "Fondavkastning": f"{fund_base + fd:.1f} %",
                col_labels[0]: nok(row_vals[0]),
                col_labels[1]: nok(row_vals[1]),
                col_labels[2]: nok(row_vals[2]),
            })

        sens_df = pd.DataFrame(rows_data)
        st.dataframe(sens_df, width="stretch", hide_index=True)

        # Vis range
        flat = [v for r in raw_values for v in r]
        st.caption(
            f"Spenn: **{nok(min(flat))}** (pessimistisk) — **{nok(max(flat))}** (optimistisk)  |  "
            f"Basis: **{nok(raw_values[1][1])}**"
        )


def render_cashflow_chart(df: pd.DataFrame) -> None:
    """Plott maanedlig netto kontantstrom fra eiendommene over tid."""
    with st.expander("💸 Kontantstrom-tidslinje — maanedlig netto CF per eiendom"):
        st.caption(
            "Viser netto maanedlig kontantstrom etter skatt og avdrag. "
            "Positiv verdi = eiendom betaler seg selv + genererer overskudd."
        )
        monthly = df[df["month"] > 0].copy()

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=monthly["date"], y=monthly["prop_net_cf"],
            name="Bolig 1 — netto CF",
            marker_color="#3b82f6", opacity=0.85,
        ))
        if monthly["prop2_net_cf"].abs().sum() > 0:
            fig.add_trace(go.Bar(
                x=monthly["date"], y=monthly["prop2_net_cf"],
                name="Bolig 2 — netto CF",
                marker_color="#67e8f9", opacity=0.85,
            ))
        fig.add_hline(y=0, line_color="#94a3b8", line_width=1)
        fig.update_layout(
            height=320,
            barmode="stack",
            xaxis=dict(title="", gridcolor="#1e2535", tickformat="%Y", dtick="M24"),
            yaxis=dict(title="kr / maned", gridcolor="#1e2535"),
            legend=dict(orientation="h", y=1.04, x=0),
            plot_bgcolor="#0b0f19",
            paper_bgcolor="#0b0f19",
            font=dict(color="#cbd5e1", size=12),
            margin=dict(l=70, r=20, t=10, b=40),
        )
        st.plotly_chart(fig, width="stretch")


def render_historical_context() -> None:
    """Vis historiske referanseverdier som kontekst for brukerens antagelser."""
    with st.expander("📚 Historiske referanseverdier (kontekst for dine antagelser)"):
        st.caption(
            "Historisk avkastning er ingen garanti for fremtidig avkastning. "
            "Tallene er nominelle og inkluderer ikke kostnader eller skatt."
        )
        hist_df = pd.DataFrame(HIST_DATA)
        st.dataframe(hist_df, width="stretch", hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Formuesimulator",
        page_icon="📊",
        layout="wide",
    )

    st.markdown("""
    <style>
        [data-testid="stMetricValue"]  { font-size: 1.25rem !important; font-weight: 700; }
        [data-testid="stMetricDelta"]  { font-size: 0.82rem !important; }
        [data-testid="stMetricLabel"]  { font-size: 0.78rem !important; color: #94a3b8; }
        .block-container               { padding-top: 1.5rem; }

        /* Mobil-tilpasning */
        @media (max-width: 640px) {
            [data-testid="stMetricValue"] { font-size: 0.95rem !important; }
            [data-testid="stMetricLabel"] { font-size: 0.70rem !important; }
            .block-container {
                padding-left: 0.5rem !important;
                padding-right: 0.5rem !important;
            }
            [data-testid="column"] { padding: 0.1rem !important; }
            h1 { font-size: 1.4rem !important; }
        }
        @media (max-width: 480px) {
            [data-testid="stMetricValue"] { font-size: 0.82rem !important; }
        }
    </style>
    """, unsafe_allow_html=True)

    st.title("📊 Formuesimulator")
    st.caption(
        "Beregn netto realformue 15 år frem i tid — inflasjonsjustert og skattekorrigert, "
        "med automatisk reinvestering av positiv kontantstrøm."
    )

    params = build_sidebar()
    df, effective_growth, total_wt = calculate_portfolio(params)

    st.subheader("Porteføljeutvikling — Netto realverdi per aktivaklasse")
    render_chart(df)

    st.subheader("Nøkkeltall")
    with st.spinner("Beregner break-even..."):
        breakeven_rate = calculate_breakeven_rate(params)
    render_metrics(df, params, effective_growth, breakeven_rate, total_wt)

    st.markdown("")
    render_yearly_table(df)

    st.markdown("")
    col_l, col_r = st.columns(2)
    with col_l:
        with st.spinner("Beregner sensitivitetsanalyse..."):
            render_sensitivity(params)
    with col_r:
        render_cashflow_chart(df)

    render_historical_context()


if __name__ == "__main__":
    main()
