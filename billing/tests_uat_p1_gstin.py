"""UAT P1 item 14 (Rudra CA): GSTIN format, state code, check character and PAN match."""
from django.test import SimpleTestCase

from billing.gst_utils import (GSTIN_CHECKSUM_MSG, GSTIN_PAN_MSG, GSTIN_STATE_MSG, gstin_check_char,
                               is_valid_gstin, validate_gstin_format)


class GstinChecksumTests(SimpleTestCase):
    def test_known_valid_vectors(self):
        for g in ('27AAPFU0939F1ZV', '29AAGCB7383J1Z4'):
            self.assertEqual(gstin_check_char(g[:14]), g[14], g)
            self.assertEqual(validate_gstin_format(g.lower()), g)

    def test_uat_gstin_21ABCDE1234F1Z5_fails_checksum(self):
        # The UAT company's GSTIN: format is fine but the check character should be 'C', not '5'.
        self.assertEqual(gstin_check_char('21ABCDE1234F1Z'), 'C')
        with self.assertRaisesMessage(ValueError, GSTIN_CHECKSUM_MSG):
            validate_gstin_format('21ABCDE1234F1Z5')
        self.assertTrue(is_valid_gstin('21ABCDE1234F1ZC'))

    def test_unchanged_legacy_value_accepted_on_format(self):
        self.assertEqual(validate_gstin_format('21ABCDE1234F1Z5', legacy='21abcde1234f1z5'), '21ABCDE1234F1Z5')
        with self.assertRaises(ValueError):
            validate_gstin_format('21ABCDE1234F1Z5', legacy='29AAGCB7383J1Z4')

    def test_state_code(self):
        for code in ('00', '39', '96', '99'):
            g = code + 'AAGCB7383J1Z'
            g = g + gstin_check_char(g)
            with self.assertRaisesMessage(ValueError, GSTIN_STATE_MSG):
                validate_gstin_format(g)
        for code in ('01', '38', '97'):
            g = code + 'AAGCB7383J1Z'
            self.assertTrue(is_valid_gstin(g + gstin_check_char(g)), code)

    def test_format_and_pan(self):
        for bad in ('27AAPFU0939F1V', '27AAPFU0939F0ZV', '27AAPFU0939F1XV', 'ABCDEFGHIJKLMNO'):
            with self.assertRaises(ValueError):
                validate_gstin_format(bad)
        self.assertEqual(validate_gstin_format('27AAPFU0939F1ZV', pan='aapfu0939f'), '27AAPFU0939F1ZV')
        with self.assertRaisesMessage(ValueError, GSTIN_PAN_MSG):
            validate_gstin_format('27AAPFU0939F1ZV', pan='AAPFU0939G')
        self.assertEqual(validate_gstin_format(''), '')
        with self.assertRaises(ValueError):
            validate_gstin_format('', required=True)
