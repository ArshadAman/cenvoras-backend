from django.urls import path
from . import views
from . import views_sidecar

urlpatterns = [
    path('products/', views.ProductListCreateView.as_view(), name='product-list-create'),
    path('warehouses/', views.WarehouseListCreateView.as_view(), name='warehouse-list-create'),
    path('stock-points/', views.stock_point_list, name='stock-point-list'),
    path('batches/', views.batch_list, name='batch-list'),
    path('transfers/', views.StockTransferListCreateView.as_view(), name='stock-transfer-list-create'),
    path('transfers/<uuid:pk>/', views.StockTransferDetailView.as_view(), name='stock-transfer-detail'),
    
    # Sidecar (BOM & Stock Journal)
    path('bom/', views_sidecar.BOMListCreateView.as_view(), name='bom-list-create'),
    path('bom/<uuid:pk>/', views_sidecar.BOMDetailView.as_view(), name='bom-detail'),
    path('stock-journals/', views_sidecar.StockJournalListCreateView.as_view(), name='stock-journal-list-create'),
    path('stock-journals/<uuid:pk>/', views_sidecar.StockJournalDetailView.as_view(), name='stock-journal-detail'),
]