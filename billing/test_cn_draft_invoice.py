"""Rudra CA (#222 follow-up): no credit note against a draft invoice."""
from datetime import date
from io import StringIO

from django.core.management import call_command

from billing.models import SalesInvoice
from billing.models_returns import CreditNote
from billing.tests_four_fixes import FourFixesBase
from billing.tests_gstr1_b2cl_cdnur_pos import _G1Helpers

MSG = 'Finalise the invoice before issuing a credit note against it.'


class CnDraftInvoiceTests(_G1Helpers, FourFixesBase):
    def _post(self, inv):
        return self.client.post("/api/billing/credit-notes/", {
            "customer": str(self.customer.id), "original_invoice": str(inv.id), "date": date.today().isoformat(),
            "reason": "other", "total_amount": "118",
            "items": [{"product": str(self.product.id), "quantity": 1, "price": "100", "tax": "18", "amount": "100"}],
        }, format="json")

    def test_create_blocked_for_draft_invoice(self):
        inv = self.inv(self.customer, "1180.00")
        SalesInvoice.objects.filter(pk=inv.pk).update(status='draft')
        res = self._post(inv)
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(MSG, res.content.decode())
        self.assertFalse(CreditNote.objects.filter(original_invoice=inv).exists())

    def test_update_blocked_when_linked_invoice_is_draft(self):
        from billing.returns_views import CreditNoteSerializer
        inv = self.inv(self.customer, "1180.00")
        cn = self.cn(inv, "100.00")
        SalesInvoice.objects.filter(pk=inv.pk).update(status='draft')
        cn.refresh_from_db()
        req = type('R', (), {'user': self.tenant})()
        s = CreditNoteSerializer(cn, data={'reason': 'other'}, partial=True, context={'request': req})
        self.assertFalse(s.is_valid())
        self.assertIn(MSG, str(s.errors))

    def test_report_lists_and_changes_nothing(self):
        inv = self.inv(self.customer, "1180.00")
        cn = self.cn(inv, "100.00")
        SalesInvoice.objects.filter(pk=inv.pk).update(status='draft')
        out = StringIO()
        call_command('list_cns_linked_to_drafts', '--tenant', str(self.tenant.id), stdout=out)
        self.assertIn(cn.credit_note_number, out.getvalue())
        self.assertIn('1 credit notes', out.getvalue())
        self.assertTrue(CreditNote.objects.filter(pk=cn.pk).exists())
