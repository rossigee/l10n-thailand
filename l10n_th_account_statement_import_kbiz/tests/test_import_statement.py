# Copyright 2023 Ross Golder (https://golder.org)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

import base64
import json
import logging

from odoo.exceptions import UserError
from odoo.modules.module import get_module_resource
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_logger = logging.getLogger(__name__)


@tagged("golder", "standard", "kbiz")
class TestParser(TransactionCase):
    """Tests for the KBiz statement file parser itself."""

    def setUp(self):
        super(TestParser, self).setUp()
        self.parser = self.env["account.statement.import.kbiz.parser"]

    def _do_parse_test(self, inputfile):
        resultfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            f"{inputfile}.json",
        )
        with open(resultfile, "r") as result:
            actual = json.load(result)

        testfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz", "tests/test_files", inputfile
        )
        with open(testfile, "rb") as data:
            res = self.parser.parse(data.read())
            self.assertTrue(res)
            for i in range(3):
                self.assertEqual(res[i], actual[i])

    def test_parse_type1_th(self):
        self._do_parse_test("type1-th.csv")

    def test_parse_type2_th(self):
        self._do_parse_test("type2-th.csv")

    def test_parse_invalid_csv(self):
        """Test parsing invalid CSV files."""
        # Test empty file - parser.parse raises on unparseable input
        with self.assertRaises(Exception):
            self.parser.parse(b"")

        # Test wrong signature
        invalid_csv = "Not a KBiz file\n"
        with self.assertRaises(Exception):
            self.parser.parse(invalid_csv.encode("utf-8"))


@tagged("golder", "standard", "kbiz")
class TestImport(TransactionCase):
    """Run test to import KBiz import."""

    def setUp(self):
        super(TestImport, self).setUp()

        # Activate THB currency
        currency = (
            self.env["res.currency"]
            .with_context(active_test=False)
            .search(
                [
                    ("name", "=", "THB"),
                ],
                limit=1,
            )
            .ensure_one()
        )
        currency.action_unarchive()
        currency_id = currency.id

        bank = self.env["res.partner.bank"].create(
            {
                "acc_number": "0123456789",
                "partner_id": self.env.ref("base.main_partner").id,
                "company_id": self.env.ref("base.main_company").id,
                "bank_id": self.env.ref("base.res_bank_1").id,
            }
        )
        suspense = self.env["res.partner.bank"].create(
            {
                "acc_number": "0123456790",
                "partner_id": self.env.ref("base.main_partner").id,
                "company_id": self.env.ref("base.main_company").id,
                # "bank_id": self.env.ref("base.res_bank_1").id,
            }
        )
        self.journal_id = self.env["account.journal"].create(
            {
                "name": "Bank Journal (KBiz test)",
                "code": "TBNKKBIZ",
                "type": "bank",
                "bank_account_id": bank.id,
                "suspense_account_id": suspense.id,
                "currency_id": currency_id,
            }
        )

    def test_statement_import(self):
        """Test correct creation of single statement."""
        resultfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv.json",
        )
        with open(resultfile, "r") as file:
            testresult = json.load(file)
            txs = testresult[2][0]["transactions"]

        testfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv",
        )
        with open(testfile, "rb") as datafile:
            kbiz_file = base64.b64encode(datafile.read())

            self.env["account.statement.import"].with_context(
                journal_id=self.journal_id.id
            ).create(
                {
                    "statement_filename": "test import",
                    "statement_file": kbiz_file,
                }
            ).import_file_button()

            bank_st_record = self.env["account.bank.statement"].search(
                [("name", "=", "2023-02")], limit=1
            )
            statement_lines = bank_st_record.line_ids

            self.assertGreater(len(txs), 0, "Fixture has no transactions to verify")

            attrs = [
                "date",
                "ref",
                "payment_ref",
                "amount",
            ]
            for tx in txs:
                foundtx = False
                for line in statement_lines:
                    matchcount = 0
                    for key in attrs:
                        if str(line[key]) == str(tx[key]):
                            matchcount += 1
                    foundtx = matchcount == len(attrs)
                    if foundtx:
                        break
                self.assertTrue(foundtx)

    def test_duplicate_import_skip(self):
        """Test that importing the same file twice skips duplicates."""
        testfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv",
        )
        with open(testfile, "rb") as datafile:
            kbiz_file = base64.b64encode(datafile.read())

            # First import
            wizard1 = self.env["account.statement.import"].with_context(
                journal_id=self.journal_id.id
            ).create(
                {
                    "statement_filename": "test import 1",
                    "statement_file": kbiz_file,
                }
            )
            result1 = wizard1.import_file_button()

            # Second import of same file should raise UserError
            wizard2 = self.env["account.statement.import"].with_context(
                journal_id=self.journal_id.id
            ).create(
                {
                    "statement_filename": "test import 2",
                    "statement_file": kbiz_file,
                }
            )
            with self.assertRaises(UserError):
                wizard2.import_file_button()

    def test_balance_calculations(self):
        """Test that balance calculations are correct."""
        resultfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv.json",
        )
        with open(resultfile, "r") as f:
            expected = json.load(f)
            expected_start = expected[2][0]["balance_start"]
            expected_end = expected[2][0]["balance_end_real"]

        testfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv",
        )
        with open(testfile, "rb") as datafile:
            kbiz_file = base64.b64encode(datafile.read())

            self.env["account.statement.import"].with_context(
                journal_id=self.journal_id.id
            ).create(
                {
                    "statement_filename": "test balance",
                    "statement_file": kbiz_file,
                }
            ).import_file_button()

            bank_st_record = self.env["account.bank.statement"].search(
                [("name", "=", "2023-02")], limit=1
            )
            self.assertTrue(bank_st_record)
            self.assertAlmostEqual(bank_st_record.balance_start, expected_start, places=2)
            self.assertAlmostEqual(bank_st_record.balance_end_real, expected_end, places=2)

    def test_unique_import_ids(self):
        """Test that unique_import_ids are generated correctly."""
        testfile = get_module_resource(
            "l10n_th_account_statement_import_kbiz",
            "tests/test_files",
            "type1-th.csv",
        )
        with open(testfile, "rb") as datafile:
            kbiz_file = base64.b64encode(datafile.read())

            self.env["account.statement.import"].with_context(
                journal_id=self.journal_id.id
            ).create(
                {
                    "statement_filename": "test unique ids",
                    "statement_file": kbiz_file,
                }
            ).import_file_button()

            bank_st_record = self.env["account.bank.statement"].search(
                [("name", "=", "2023-02")], limit=1
            )
            lines = bank_st_record.line_ids
            for line in lines:
                self.assertTrue(line.unique_import_id)
                # Ensure no duplicates in this import
                duplicate_lines = lines.filtered(lambda l: l.unique_import_id == line.unique_import_id)
                self.assertEqual(len(duplicate_lines), 1)
