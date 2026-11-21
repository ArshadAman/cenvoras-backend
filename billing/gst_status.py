"""
UAT P1 item 13 rework (Rudra CA): the company's GST status drives the document and returns.

  regular       GSTIN required. Document 'Tax Invoice'; all Rule 46 checks; GSTR-1/3B available.
  composition   GSTIN required. Document 'Bill of Supply' carrying the declaration
                'composition taxable person, not eligible to collect tax on supplies';
                tax locked at 0; GSTR-1/3B hidden.
  unregistered  No GSTIN. Document 'Invoice' (never 'Tax Invoice'); tax locked at 0 (s.32);
                GST returns hidden; gentle s.22 threshold reminder as turnover nears the limit.

Existing companies get a stored value from data (GSTIN -> regular, none -> unregistered) via
migration users/0024 which only fills empty values. A company whose value is still empty
(e.g. a fresh signup before setup) keeps today's behaviour: nothing is locked or hidden, and the
derived status is only shown.
"""
REGULAR = 'regular'
COMPOSITION = 'composition'
UNREGISTERED = 'unregistered'
CHOICES = [
    (REGULAR, 'Regular'),
    (COMPOSITION, 'Composition'),
    (UNREGISTERED, 'Not registered'),
]
COMPOSITION_DECLARATION = 'composition taxable person, not eligible to collect tax on supplies'
DOCUMENT_TITLES = {REGULAR: 'Tax Invoice', COMPOSITION: 'Bill of Supply', UNREGISTERED: 'Invoice'}
RETURNS_HIDDEN_MSG = ('GST returns are not available: the company GST status is {label}. '
                      'Change it in Settings if the business is now a regular GST registrant.')


def _tenant(user):
    return getattr(user, 'active_tenant', None) or user


def derive_gst_status(user):
    """Default from data: a company with a GSTIN is Regular, one without is Not registered."""
    t = _tenant(user)
    return REGULAR if (getattr(t, 'gstin', '') or '').strip() else UNREGISTERED


def stored_gst_status(user):
    """The explicitly stored status, or None (legacy/unset: no locking, nothing hidden)."""
    t = _tenant(user)
    if str(getattr(t, 'country', 'IN') or 'IN').upper() != 'IN':
        return None
    return getattr(t, 'gst_status', None) or None


def gst_status(user):
    """Status to show: stored value, else derived."""
    return stored_gst_status(user) or derive_gst_status(user)


def tax_locked(user):
    return stored_gst_status(user) in (COMPOSITION, UNREGISTERED)


def gst_returns_available(user):
    return stored_gst_status(user) in (None, REGULAR)


def rule46_applies(user):
    return stored_gst_status(user) in (None, REGULAR)


def document_title(user):
    return DOCUMENT_TITLES[gst_status(user)]


def document_declaration(user):
    return COMPOSITION_DECLARATION if gst_status(user) == COMPOSITION else None


def returns_hidden_message(user):
    label = dict(CHOICES).get(stored_gst_status(user) or '', '')
    return RETURNS_HIDDEN_MSG.format(label=label)


CONFIRM_BANNER = 'Confirm your GST status (Regular / Composition / Not registered)'


def needs_confirmation(user):
    """Rudra (#216): the no-status fallback is temporary. Indian companies whose status is empty,
    or was filled by the migration and not yet confirmed (a composition dealer also has a GSTIN),
    get an admin banner. Saving a status (settings or setup) confirms it."""
    t = _tenant(user)
    if str(getattr(t, 'country', 'IN') or 'IN').upper() != 'IN':
        return False
    return not getattr(t, 'gst_status', None) or not getattr(t, 'gst_status_confirmed', False)
