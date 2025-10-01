from django.contrib import admin
from .models import Account, GeneralLedgerEntry

admin.site.register([Account, GeneralLedgerEntry])
