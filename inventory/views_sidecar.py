from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models_sidecar import BillOfMaterial, StockJournal, StockJournalItem
from .serializers_sidecar import BillOfMaterialSerializer, StockJournalSerializer


class BOMListCreateView(generics.ListCreateAPIView):
    serializer_class = BillOfMaterialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return BillOfMaterial.objects.filter(created_by=self.request.user.active_tenant)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_tenant)


class BOMDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BillOfMaterialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return BillOfMaterial.objects.filter(created_by=self.request.user.active_tenant)


class StockJournalListCreateView(generics.ListCreateAPIView):
    serializer_class = StockJournalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockJournal.objects.filter(created_by=self.request.user.active_tenant).order_by('-date')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_tenant)
        # TODO: Implement Stock Ledger Updates here (Feature 11)
        # For phase 1, we just record the journal. Phase 2/3 will link to StockPoint updates.
        # Actually, if we want stock updates now, we should do it.
        # The prompt says "Feature 11: Create generic StockJournal model" [x].
        # It doesn't explicitly say "Update stock automatically".
        # But a journal without effect is just a record. 
        # Given "Inventory & Stock Engine (Backend Completed)", let's assume basic recording is enough for now, 
        # or we add a signal handler. For UI task, listing/creating is priority.


class StockJournalDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockJournalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockJournal.objects.filter(created_by=self.request.user.active_tenant)
