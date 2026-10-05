import os
import tempfile
import unittest

from finanzas.app import create_app


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(os.path.join(self.tmp.name, "test.db"))
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, data, status=201):
        res = self.client.post(path, json=data)
        self.assertEqual(res.status_code, status, res.get_json())
        return res.get_json()

    def category_id(self, name):
        cats = self.client.get("/api/categories?month=2026-09").get_json()
        return next(c["id"] for c in cats if c["name"] == name)

    def test_default_categories_seeded(self):
        cats = self.client.get("/api/categories").get_json()
        self.assertTrue(any(c["name"] == "Mercado" for c in cats))
        self.assertTrue(any(c["type"] == "income" for c in cats))

    def test_card_balance_and_available_across_months(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000,
                                        "initial_balance": 100, "cut_day": 15, "due_day": 31,
                                        "due_next_month": "0"})
        self.post("/api/transactions", {"date": "2026-08-10", "amount": 300, "type": "expense",
                                        "payment_method": "card", "card_id": card["id"]})
        self.post("/api/transactions", {"date": "2026-09-05", "amount": 200, "type": "card_payment",
                                        "card_id": card["id"]})

        aug = self.client.get("/api/cards?month=2026-08").get_json()[0]
        self.assertEqual(aug["balance"], 400)
        self.assertEqual(aug["available"], 600)
        self.assertEqual(aug["utilization"], 40)

        sep = self.client.get("/api/cards?month=2026-09").get_json()[0]
        self.assertEqual(sep["balance"], 200)
        self.assertEqual(sep["month_paid"], 200)
        self.assertEqual(sep["due_date"], "2026-09-30")  # día 31 ajustado a septiembre

    def test_card_payment_is_not_an_expense(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000})
        self.post("/api/transactions", {"date": "2026-09-01", "amount": 5000, "type": "income"})
        self.post("/api/transactions", {"date": "2026-09-02", "amount": 300, "type": "expense",
                                        "payment_method": "card", "card_id": card["id"]})
        self.post("/api/transactions", {"date": "2026-09-03", "amount": 300, "type": "card_payment",
                                        "card_id": card["id"]})
        d = self.client.get("/api/dashboard?month=2026-09").get_json()
        self.assertEqual(d["totals"]["expense"], 300)
        self.assertEqual(d["totals"]["income"], 5000)
        self.assertEqual(d["totals"]["balance"], 4700)
        self.assertEqual(len(d["trend"]), 12)
        self.assertEqual(len(d["daily"]["current"]), 30)

    def test_monthly_budget_override_and_copy(self):
        mercado = self.category_id("Mercado")
        cat = self.client.put(f"/api/categories/{mercado}", json={
            "name": "Mercado", "type": "expense", "default_budget": 500}).get_json()
        self.assertEqual(cat["default_budget"], 500)
        self.client.put("/api/budgets", json={"category_id": mercado, "month": "2026-09", "amount": 800})

        def budget(month):
            cats = self.client.get(f"/api/categories?month={month}").get_json()
            return next(c["budget"] for c in cats if c["id"] == mercado)

        self.assertEqual(budget("2026-09"), 800)
        self.assertEqual(budget("2026-10"), 500)
        self.client.post("/api/budgets/copy", json={"from": "2026-09", "to": "2026-10"})
        self.assertEqual(budget("2026-10"), 800)
        self.client.put("/api/budgets", json={"category_id": mercado, "month": "2026-10", "amount": ""})
        self.assertEqual(budget("2026-10"), 500)

    def test_budget_alert(self):
        mercado = self.category_id("Mercado")
        self.client.put("/api/budgets", json={"category_id": mercado, "month": "2026-09", "amount": 100})
        self.post("/api/transactions", {"date": "2026-09-02", "amount": 150, "type": "expense",
                                        "category_id": mercado})
        d = self.client.get("/api/dashboard?month=2026-09").get_json()
        self.assertTrue(any("Mercado" in a["text"] and a["level"] == "danger" for a in d["alerts"]))

    def test_recurring_generation_is_idempotent(self):
        self.post("/api/recurring", {"description": "Arriendo", "amount": 1000, "type": "expense",
                                     "day": 31})
        first = self.client.post("/api/recurring/generate", json={"month": "2026-02"}).get_json()
        second = self.client.post("/api/recurring/generate", json={"month": "2026-02"}).get_json()
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        txs = self.client.get("/api/transactions?month=2026-02").get_json()
        self.assertEqual(txs[0]["date"], "2026-02-28")

    def test_validation_errors(self):
        res = self.client.post("/api/transactions", json={"date": "2026-09-01", "amount": -5,
                                                          "type": "expense"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/transactions", json={"date": "2026-09-01", "amount": 5,
                                                          "type": "expense", "payment_method": "card"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("tarjeta", res.get_json()["error"])
        res = self.client.get("/api/dashboard?month=2026-13")
        self.assertEqual(res.status_code, 400)

    def test_filters_and_csv_export(self):
        self.post("/api/transactions", {"date": "2026-09-01", "amount": 10, "type": "expense",
                                        "description": "Café"})
        self.post("/api/transactions", {"date": "2026-09-02", "amount": 20, "type": "income",
                                        "description": "Venta"})
        only_exp = self.client.get("/api/transactions?month=2026-09&type=expense").get_json()
        self.assertEqual([t["description"] for t in only_exp], ["Café"])
        search = self.client.get("/api/transactions?month=2026-09&q=vent").get_json()
        self.assertEqual(len(search), 1)
        csv_text = self.client.get("/api/transactions/export.csv?month=2026-09").get_data(as_text=True)
        self.assertIn("Café", csv_text)
        self.assertIn("Venta", csv_text)

    def test_card_payment_with_category(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000})
        cat = self.post("/api/categories", {"name": "Pago tarjetas", "type": "expense"})
        tx = self.post("/api/transactions", {"date": "2026-09-20", "amount": 400, "type": "card_payment",
                                             "card_id": card["id"], "category_id": cat["id"]})
        self.assertEqual(tx["category_name"], "Pago tarjetas")
        cats = self.client.get("/api/categories?month=2026-09").get_json()
        self.assertEqual(next(c["spent"] for c in cats if c["id"] == cat["id"]), 400)
        d = self.client.get("/api/dashboard?month=2026-09").get_json()
        self.assertEqual(d["totals"]["expense"], 0)  # sigue sin contarse como gasto
        self.assertEqual(d["totals"]["card_payments"], 400)
        # una categoría de ingreso no se puede usar en un pago de tarjeta
        salario = self.category_id("Salario")
        res = self.client.post("/api/transactions", json={
            "date": "2026-09-20", "amount": 10, "type": "card_payment",
            "card_id": card["id"], "category_id": salario})
        self.assertEqual(res.status_code, 400)

    def test_register_single_recurring(self):
        luz = self.post("/api/recurring", {"description": "Energía", "amount": 100, "type": "expense", "day": 10})
        agua = self.post("/api/recurring", {"description": "Agua", "amount": 50, "type": "expense", "day": 12})
        # Se registra solo uno, con un monto distinto al habitual
        self.post("/api/transactions", {"date": "2026-09-10", "amount": 130, "type": "expense",
                                        "description": "Energía", "recurring_id": luz["id"]})
        recs = {r["id"]: r for r in self.client.get("/api/recurring?month=2026-09").get_json()}
        self.assertTrue(recs[luz["id"]]["registered"])
        self.assertFalse(recs[agua["id"]]["registered"])
        # No se puede registrar dos veces en el mismo mes
        res = self.client.post("/api/transactions", json={
            "date": "2026-09-11", "amount": 130, "type": "expense", "recurring_id": luz["id"]})
        self.assertEqual(res.status_code, 400)
        # "Registrar todos" solo crea el que faltaba
        gen = self.client.post("/api/recurring/generate", json={"month": "2026-09"}).get_json()
        self.assertEqual(gen["created"], 1)
        # En el mes siguiente vuelve a quedar pendiente
        recs = {r["id"]: r for r in self.client.get("/api/recurring?month=2026-10").get_json()}
        self.assertFalse(recs[luz["id"]]["registered"])

    def test_income_period_override(self):
        # Ingreso pagado el 30 de septiembre que corresponde a octubre
        tx = self.post("/api/transactions", {"date": "2026-09-30", "amount": 5000, "type": "income",
                                             "period": "2026-10"})
        self.assertEqual(tx["period"], "2026-10")
        sep = self.client.get("/api/dashboard?month=2026-09").get_json()["totals"]
        oct_ = self.client.get("/api/dashboard?month=2026-10").get_json()["totals"]
        self.assertEqual(sep["income"], 0)
        self.assertEqual(oct_["income"], 5000)
        listed = self.client.get("/api/transactions?month=2026-10").get_json()
        self.assertEqual([t["id"] for t in listed], [tx["id"]])
        # Sin período explícito se usa el mes de la fecha
        tx2 = self.post("/api/transactions", {"date": "2026-09-30", "amount": 10, "type": "expense"})
        self.assertEqual(tx2["period"], "2026-09")
        # Gasto con fecha fuera del mes se ubica en el primer día en el ritmo diario
        self.client.put(f"/api/transactions/{tx2['id']}", json={
            "date": "2026-09-30", "amount": 10, "type": "expense", "period": "2026-10"})
        d = self.client.get("/api/dashboard?month=2026-10").get_json()
        self.assertEqual(d["daily"]["current"][0], 10)
        res = self.client.post("/api/transactions", json={"date": "2026-09-30", "amount": 1,
                                                          "type": "income", "period": "oct"})
        self.assertEqual(res.status_code, 400)

    def test_income_shift_day_setting(self):
        self.client.put("/api/settings", json={"income_shift_day": 25})
        late = self.post("/api/transactions", {"date": "2026-09-29", "amount": 100, "type": "income"})
        early = self.post("/api/transactions", {"date": "2026-09-10", "amount": 100, "type": "income"})
        expense = self.post("/api/transactions", {"date": "2026-09-29", "amount": 100, "type": "expense"})
        self.assertEqual(late["period"], "2026-10")
        self.assertEqual(early["period"], "2026-09")
        self.assertEqual(expense["period"], "2026-09")
        # El salario fijo del día 29 para octubre se fecha el 29 de septiembre
        sal = self.post("/api/recurring", {"description": "Salario", "amount": 100, "type": "income", "day": 29})
        recs = self.client.get("/api/recurring?month=2026-10").get_json()
        self.assertEqual(recs[0]["expected_date"], "2026-09-29")
        self.client.post("/api/recurring/generate", json={"month": "2026-10"})
        txs = self.client.get("/api/transactions?month=2026-10&q=Salario").get_json()
        self.assertEqual([(t["date"], t["period"]) for t in txs], [("2026-09-29", "2026-10")])
        self.assertTrue(self.client.get("/api/recurring?month=2026-10").get_json()[0]["registered"])
        self.assertFalse(self.client.get("/api/recurring?month=2026-09").get_json()[0]["registered"])
        # Se puede desactivar
        self.client.put("/api/settings", json={"income_shift_day": ""})
        again = self.post("/api/transactions", {"date": "2026-09-29", "amount": 100, "type": "income"})
        self.assertEqual(again["period"], "2026-09")
        self.assertIsNotNone(sal)

    def test_investment_portfolio(self):
        etf = self.post("/api/investments", {"name": "ETF", "platform": "Trii", "asset_type": "etf",
                                             "date": "2026-08-05", "invested": 1000, "value": 1100})
        self.post("/api/investments", {"name": "CDT", "platform": "Bancolombia",
                                        "asset_type": "renta_fija", "date": "2026-09-10",
                                        "invested": 900})
        aug = self.client.get("/api/investments?month=2026-08").get_json()
        self.assertEqual(aug["totals"]["value"], 1100)
        self.assertEqual(aug["totals"]["gain"], 100)
        self.assertEqual(len(aug["holdings"]), 1)  # el CDT aún no existía

        # Aporte, actualización de valor y retiro en septiembre
        self.post(f"/api/investments/{etf['id']}/moves", {"kind": "contribution", "date": "2026-09-01", "amount": 500})
        self.post(f"/api/investments/{etf['id']}/moves", {"kind": "valuation", "date": "2026-09-20", "amount": 2000})
        self.post(f"/api/investments/{etf['id']}/moves", {"kind": "withdrawal", "date": "2026-09-25", "amount": 1000})
        sep = self.client.get("/api/investments?month=2026-09").get_json()
        h = {x["name"]: x for x in sep["holdings"]}
        self.assertEqual(h["ETF"]["value"], 1000)
        self.assertEqual(h["ETF"]["invested"], 750)  # el retiro saca la mitad del capital
        self.assertEqual(sep["totals"]["value"], 1900)
        self.assertEqual(h["CDT"]["share"], round(900 / 1900 * 100, 1))
        platforms = {p["name"]: p["share"] for p in sep["by_platform"]}
        self.assertEqual(set(platforms), {"Trii", "Bancolombia"})
        self.assertEqual({t["name"] for t in sep["by_type"]}, {"etf", "renta_fija"})
        self.assertEqual(sep["history"][-1], {"month": "2026-09", "value": 1900, "invested": 1650})
        self.assertEqual(sep["history"][-2]["value"], 1100)

        moves = self.client.get(f"/api/investments/{etf['id']}/moves").get_json()
        self.assertEqual(moves[0]["kind"], "withdrawal")
        self.assertEqual(moves[0]["value_after"], 1000)
        self.client.delete(f"/api/investments/moves/{moves[0]['id']}")
        sep = self.client.get("/api/investments?month=2026-09").get_json()
        self.assertEqual(sep["totals"]["value"], 2900)

        d = self.client.get("/api/dashboard?month=2026-09").get_json()
        self.assertEqual(d["investments"], 2900)

        res = self.client.post("/api/investments", json={"name": "X", "asset_type": "nope",
                                                         "date": "2026-09-01", "invested": 1})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.delete(f"/api/investments/{etf['id']}").status_code, 204)

    def test_investments_in_dollars(self):
        self.post("/api/investments", {"name": "VOO", "platform": "Trii", "asset_type": "etf",
                                        "currency": "USD", "date": "2026-09-05",
                                        "invested": 100, "value": 110})
        self.post("/api/investments", {"name": "CDT", "platform": "Nu", "asset_type": "renta_fija",
                                        "date": "2026-09-05", "invested": 1000})
        # Sin TRM el dólar no se puede convertir
        sep = self.client.get("/api/investments?month=2026-09").get_json()
        self.assertTrue(sep["needs_rate"])
        self.assertEqual(sep["totals"]["value"], 1000)

        self.client.put("/api/fx-rates", json={"month": "2026-09", "rate": 10})
        sep = self.client.get("/api/investments?month=2026-09").get_json()
        self.assertFalse(sep["needs_rate"])
        voo = next(h for h in sep["holdings"] if h["name"] == "VOO")
        self.assertEqual((voo["value_native"], voo["value"], voo["gain_pct"]), (110, 1100, 10))
        self.assertEqual(sep["totals"]["value"], 2100)
        self.assertEqual({c["name"] for c in sep["by_currency"]}, {"USD", "COP"})

        usd = self.client.get("/api/investments?month=2026-09&display=USD").get_json()
        self.assertEqual(usd["totals"]["value"], 210)
        # La TRM de septiembre se sigue usando en octubre hasta que se defina otra
        self.client.put("/api/fx-rates", json={"month": "2026-11", "rate": 20})
        oct_ = self.client.get("/api/investments?month=2026-10").get_json()
        self.assertEqual(oct_["rate"], 10)
        nov = self.client.get("/api/investments?month=2026-11").get_json()
        self.assertEqual(nov["totals"]["value"], 3200)
        self.assertEqual(nov["history"][-1]["value"], 3200)
        self.assertEqual(nov["history"][-2]["value"], 2100)
        self.assertEqual(self.client.get("/api/dashboard?month=2026-11").get_json()["investments"], 3200)

        res = self.client.post("/api/investments", json={"name": "X", "currency": "EUR",
                                                         "date": "2026-09-01", "invested": 1})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.put("/api/fx-rates", json={"month": "2026-09", "rate": 0}).status_code, 400)

    def test_due_date_next_month(self):
        # Corte 10 y pago 25: por defecto el pago es el mes siguiente; se puede elegir el mismo mes
        same = self.post("/api/cards", {"name": "A", "credit_limit": 100, "cut_day": 10, "due_day": 25,
                                        "due_next_month": "0"})
        nxt = self.post("/api/cards", {"name": "B", "credit_limit": 100, "cut_day": 10, "due_day": 25})
        auto = self.post("/api/cards", {"name": "C", "credit_limit": 100, "cut_day": 30, "due_day": 15})
        cards = {c["name"]: c for c in self.client.get("/api/cards?month=2026-10").get_json()}
        self.assertEqual((cards["A"]["cut_date"], cards["A"]["due_date"]), ("2026-10-10", "2026-10-25"))
        self.assertEqual(cards["B"]["due_date"], "2026-11-25")
        self.assertTrue(cards["B"]["due_next_month"])
        self.assertEqual(cards["C"]["due_date"], "2026-11-15")  # pago antes del corte: mes siguiente
        dec = {c["name"]: c for c in self.client.get("/api/cards?month=2026-12").get_json()}
        self.assertEqual(dec["B"]["due_date"], "2027-01-25")
        self.assertIsNotNone(same and nxt and auto)

    def test_debit_cards(self):
        debit = self.post("/api/cards", {"kind": "debit", "name": "Débito Nu", "credit_limit": 999,
                                         "cut_day": 5})
        credit = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000})
        self.assertEqual((debit["kind"], debit["credit_limit"], debit["cut_day"]), ("debit", 0, None))
        tx = self.post("/api/transactions", {"date": "2026-10-03", "amount": 50, "type": "expense",
                                             "payment_method": "debit", "card_id": debit["id"]})
        self.assertEqual(tx["card_name"], "Débito Nu")
        # Sin tarjeta también se puede pagar con débito
        self.post("/api/transactions", {"date": "2026-10-03", "amount": 5, "type": "expense",
                                        "payment_method": "debit"})
        cards = {c["name"]: c for c in self.client.get("/api/cards?month=2026-10").get_json()}
        self.assertEqual((cards["Débito Nu"]["month_spent"], cards["Débito Nu"]["balance"]), (50, 0))
        d = self.client.get("/api/dashboard?month=2026-10").get_json()
        self.assertEqual([c["name"] for c in d["cards"]], ["Visa"])
        self.assertEqual(d["card_totals"]["limit"], 1000)
        # Tipos de tarjeta incorrectos
        bad = [
            {"type": "expense", "payment_method": "card", "card_id": debit["id"]},
            {"type": "card_payment", "card_id": debit["id"]},
            {"type": "expense", "payment_method": "debit", "card_id": credit["id"]},
        ]
        for extra in bad:
            res = self.client.post("/api/transactions", json={"date": "2026-10-03", "amount": 1, **extra})
            self.assertEqual(res.status_code, 400, extra)

    def test_transaction_type_list_filter(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000})
        self.post("/api/transactions", {"date": "2026-10-01", "amount": 1, "type": "expense"})
        self.post("/api/transactions", {"date": "2026-10-01", "amount": 2, "type": "income"})
        self.post("/api/transactions", {"date": "2026-10-01", "amount": 3, "type": "card_payment",
                                        "card_id": card["id"]})
        out = self.client.get("/api/transactions?month=2026-10&type=expense,card_payment").get_json()
        self.assertEqual(sorted(t["type"] for t in out), ["card_payment", "expense"])
        inc = self.client.get("/api/transactions?month=2026-10&type=income").get_json()
        self.assertEqual([t["type"] for t in inc], ["income"])
        # "Registrar todos" puede limitarse a gastos o ingresos
        self.post("/api/recurring", {"description": "Arriendo", "amount": 10, "type": "expense", "day": 1})
        self.post("/api/recurring", {"description": "Salario", "amount": 20, "type": "income", "day": 1})
        gen = self.client.post("/api/recurring/generate", json={"month": "2026-11", "type": "income"}).get_json()
        self.assertEqual(gen["created"], 1)

    def test_installment_plan(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 5_000_000, "cut_day": 10,
                                        "due_day": 25, "interest_rate": 1.9})
        mercado = self.category_id("Mercado")
        tx = self.post("/api/transactions", {
            "date": "2026-10-03", "amount": 1_200_000, "type": "expense", "category_id": mercado,
            "payment_method": "card", "card_id": card["id"], "installments": 6,
            "financed": "1", "rate": 2, "description": "Nevera"})
        self.assertEqual((tx["financed"], tx["rate"]), (1, 2))

        def totals(month):
            return self.client.get(f"/api/dashboard?month={month}").get_json()["totals"]

        # La compra no suma completa en octubre; la cuota 1 (corte 10 oct) se paga en noviembre
        self.assertEqual(totals("2026-10")["expense"], 0)
        self.assertEqual(totals("2026-11")["expense"], 200_000 + 24_000)
        self.assertEqual(totals("2026-12")["expense"], 200_000 + 20_000)
        cats = {c["name"]: c["spent"] for c in self.client.get("/api/categories?month=2026-11").get_json()}
        self.assertEqual((cats["Mercado"], cats["Intereses"]), (200_000, 24_000))

        # La deuda sube por el total al comprar y por los intereses en cada corte
        oct_card = self.client.get("/api/cards?month=2026-10").get_json()[0]
        self.assertEqual(oct_card["balance"], 1_224_000)
        self.assertEqual(oct_card["statement_payment"], 224_000)
        plan = self.client.get("/api/cards?month=2026-11").get_json()[0]["plans"][0]
        self.assertEqual((plan["current_number"], plan["remaining"], plan["pending_capital"]),
                         (1, 5, 1_000_000))
        self.assertEqual(plan["last_period"], "2027-04")

        # En movimientos de noviembre aparecen la cuota y sus intereses; la compra en octubre no suma
        nov = self.client.get("/api/transactions?month=2026-11").get_json()
        self.assertEqual(sorted((t["installment_label"], t["amount"]) for t in nov),
                         [("Cuota 1/6", 24_000), ("Cuota 1/6", 200_000)])
        octo = self.client.get("/api/transactions?month=2026-10").get_json()
        self.assertEqual([t["counts"] for t in octo], [False])

        # Abono a capital el 15 de nov: las cuotas que faltan desde el corte de diciembre bajan
        self.post("/api/transactions", {"date": "2026-11-15", "amount": 400_000, "type": "card_payment",
                                        "card_id": card["id"], "applies_to": tx["id"]})
        dec = self.client.get("/api/cards?month=2026-12").get_json()[0]["plans"][0]
        self.assertEqual(dec["current_number"], 2)
        jan = self.client.get("/api/transactions?month=2027-01").get_json()
        capital = next(t["amount"] for t in jan if t["installment_part"] == "capital")
        self.assertAlmostEqual(capital, (1_000_000 - 1_000_000 / 5 - 400_000) / 4, places=1)
        ahead = self.client.get("/api/dashboard?month=2026-11").get_json()["installments_ahead"]
        self.assertEqual(ahead[0]["month"], "2026-11")
        self.assertEqual(ahead[0]["amount"], 224_000)

        # Un abono solo aplica a compras a cuotas de la misma tarjeta
        other = self.post("/api/cards", {"name": "Master", "credit_limit": 100})
        res = self.client.post("/api/transactions", json={
            "date": "2026-11-15", "amount": 1, "type": "card_payment", "card_id": other["id"],
            "applies_to": tx["id"]})
        self.assertEqual(res.status_code, 400)

        # Al borrar la compra desaparece su plan
        self.client.delete(f"/api/transactions/{tx['id']}")
        self.assertEqual(totals("2026-11")["expense"], 0)

    def test_installment_rate_defaults_to_card_and_legacy_purchases(self):
        card = self.post("/api/cards", {"name": "Visa", "credit_limit": 1000, "cut_day": 10,
                                        "due_day": 25, "interest_rate": 1.5})
        planned = self.post("/api/transactions", {
            "date": "2026-10-01", "amount": 300, "type": "expense", "payment_method": "card",
            "card_id": card["id"], "installments": 3, "financed": True})
        self.assertEqual(planned["rate"], 1.5)
        # Compra con cuotas registrada como antes (sin plan): cuenta completa y queda por revisar
        legacy = self.post("/api/transactions", {
            "date": "2026-10-02", "amount": 600, "type": "expense", "payment_method": "card",
            "card_id": card["id"], "installments": 6})
        self.assertEqual(legacy["financed"], 0)
        c = self.client.get("/api/cards?month=2026-10").get_json()[0]
        self.assertEqual([u["id"] for u in c["unplanned"]], [legacy["id"]])
        self.assertEqual(self.client.get("/api/dashboard?month=2026-10").get_json()["totals"]["expense"], 600)
        # Revisarla = editarla y marcarla como compra a cuotas
        self.client.put(f"/api/transactions/{legacy['id']}", json={
            "date": "2026-10-02", "amount": 600, "type": "expense", "payment_method": "card",
            "card_id": card["id"], "installments": 6, "financed": "1", "rate": 0})
        c = self.client.get("/api/cards?month=2026-10").get_json()[0]
        self.assertEqual(c["unplanned"], [])
        self.assertEqual(self.client.get("/api/dashboard?month=2026-11").get_json()["totals"]["expense"],
                         round(100 + 300 * 0.015 + 100, 2))
        # Cambiar el corte de la tarjeta recalcula los planes
        self.client.put(f"/api/cards/{card['id']}", json={
            "name": "Visa", "credit_limit": 1000, "cut_day": 30, "due_day": 15, "interest_rate": 1.5})
        self.assertEqual(self.client.get("/api/dashboard?month=2026-11").get_json()["totals"]["expense"],
                         round(100 + 300 * 0.015 + 100, 2))

    def test_goals_contribution(self):
        goal = self.post("/api/goals", {"name": "Viaje", "target": 1000})
        res = self.client.post(f"/api/goals/{goal['id']}/contribute", json={"amount": 300}).get_json()
        self.assertEqual(res["saved"], 300)
        res = self.client.post(f"/api/goals/{goal['id']}/contribute", json={"amount": -500}).get_json()
        self.assertEqual(res["saved"], 0)

    def test_restore_backup(self):
        import io
        self.post("/api/goals", {"name": "Viaje", "target": 1000})
        backup = self.client.get("/api/backup").data
        self.post("/api/goals", {"name": "Carro", "target": 5000})
        self.assertEqual(len(self.client.get("/api/goals").get_json()), 2)

        res = self.client.post("/api/restore", data={"file": (io.BytesIO(backup), "respaldo.db")},
                               content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual([g["name"] for g in self.client.get("/api/goals").get_json()], ["Viaje"])

        res = self.client.post("/api/restore", data={"file": (io.BytesIO(b"hola"), "x.db")},
                               content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("db_path", self.client.get("/api/info").get_json())

    def test_shutdown(self):
        # Sin run.py no hay forma de cerrar la app
        self.assertFalse(self.client.get("/api/info").get_json()["can_shutdown"])
        self.assertEqual(self.client.post("/api/shutdown").status_code, 400)
        import threading
        called = threading.Event()
        self.app.config["SHUTDOWN"] = called.set
        self.assertTrue(self.client.get("/api/info").get_json()["can_shutdown"])
        self.assertEqual(self.client.post("/api/shutdown").status_code, 200)
        self.assertTrue(called.wait(3))

    def test_backup_download(self):
        res = self.client.get("/api/backup")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.startswith(b"SQLite format 3"))

    def test_index_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Mis Finanzas", res.data)
        res.close()


if __name__ == "__main__":
    unittest.main()
