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
                                        "initial_balance": 100, "cut_day": 15, "due_day": 31})
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

    def test_goals_contribution(self):
        goal = self.post("/api/goals", {"name": "Viaje", "target": 1000})
        res = self.client.post(f"/api/goals/{goal['id']}/contribute", json={"amount": 300}).get_json()
        self.assertEqual(res["saved"], 300)
        res = self.client.post(f"/api/goals/{goal['id']}/contribute", json={"amount": -500}).get_json()
        self.assertEqual(res["saved"], 0)

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
