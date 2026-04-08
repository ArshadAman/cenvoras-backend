from django.urls import path
from . import views
from . import views_sidecar

urlpatterns = [
    path('products/', views.ProductListCreateView.as_view(), name='product-list-create'),
    path('products/<uuid:pk>/', views.ProductDetailView.as_view(), name='product-detail'),
    path('products/template/csv/', views.download_product_csv_template, name='product-csv-template'),
    path('products/bulk-upload/csv/', views.bulk_upload_products, name='product-bulk-upload-csv'),
    path('warehouses/', views.WarehouseListCreateView.as_view(), name='warehouse-list-create'),
    path('warehouses/<uuid:pk>/', views.WarehouseDetailView.as_view(), name='warehouse-detail'),
    path('stock-points/', views.stock_point_list, name='stock-point-list'),
    path('batches/', views.batch_list, name='batch-list'),
    path('batches/split/', views.batch_split, name='batch-split'),
    path('transfers/', views.StockTransferListCreateView.as_view(), name='stock-transfer-list-create'),
    path('transfers/<uuid:pk>/', views.StockTransferDetailView.as_view(), name='stock-transfer-detail'),
    
    # Reports
    path('reports/expiry/', views.expiry_report, name='expiry-report'),
    path('reports/shortage/', views.shortage_report, name='shortage-report'),
    path('reports/warranty/', views.warranty_report, name='warranty-report'),
    path('reports/expiry-summary/', views.expiry_dashboard_summary, name='expiry-dashboard-summary'),
    
    # Sidecar (BOM & Stock Journal)
    path('bom/', views_sidecar.BOMListCreateView.as_view(), name='bom-list-create'),
    path('bom/<uuid:pk>/', views_sidecar.BOMDetailView.as_view(), name='bom-detail'),
    path('stock-journals/', views_sidecar.StockJournalListCreateView.as_view(), name='stock-journal-list-create'),
    path('stock-journals/<uuid:pk>/', views_sidecar.StockJournalDetailView.as_view(), name='stock-journal-detail'),
    
    # Price Lists & Schemes
    path('price-lists/', views.PriceListListCreateView.as_view(), name='price-list-list-create'),
    path('price-lists/<uuid:pk>/', views.PriceListDetailView.as_view(), name='price-list-detail'),
    path('schemes/', views.SchemeListCreateView.as_view(), name='scheme-list-create'),
    path('schemes/<uuid:pk>/', views.SchemeDetailView.as_view(), name='scheme-detail'),
]