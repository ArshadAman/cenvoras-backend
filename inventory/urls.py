from django.urls import path
from . import views

urlpatterns = [
    path('products/', views.ProductListCreateView.as_view(), name='product-list-create'),
    path('warehouses/', views.WarehouseListCreateView.as_view(), name='warehouse-list-create'),
    path('stock-points/', views.stock_point_list, name='stock-point-list'),
    path('transfers/', views.StockTransferListCreateView.as_view(), name='stock-transfer-list-create'),
    path('transfers/<uuid:pk>/', views.StockTransferDetailView.as_view(), name='stock-transfer-detail'),
]