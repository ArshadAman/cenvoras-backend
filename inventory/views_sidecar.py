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
        if getattr(self, 'swagger_fake_view', False): return BillOfMaterial.objects.none()
        return BillOfMaterial.objects.filter(created_by=self.request.user.active_tenant)


class StockJournalListCreateView(generics.ListCreateAPIView):
    serializer_class = StockJournalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = StockJournal.objects.filter(created_by=self.request.user.active_tenant).order_by('-date')
        
        # Filtering logic
        search = self.request.query_params.get('search', '').strip()
        warehouse_id = self.request.query_params.get('warehouse')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        product_name = self.request.query_params.get('product')
        
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(voucher_no__icontains=search) | 
                Q(id__icontains=search) | 
                Q(notes__icontains=search)
            )
            
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
            
        if date_from:
            qs = qs.filter(date__gte=date_from)
            
        if date_to:
            qs = qs.filter(date__lte=date_to)
            
        if product_name:
            qs = qs.filter(items__product__name__icontains=product_name).distinct()

        return qs

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
        if getattr(self, 'swagger_fake_view', False): return StockJournal.objects.none()
        return StockJournal.objects.filter(created_by=self.request.user.active_tenant)
