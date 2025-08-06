from .views import add_product, upload_products_csv, product_update_delete, list_products
from django.urls import path
urlpatterns = [
    path('add-product/', add_product, name='add_product'),
    path('upload-products-csv/', upload_products_csv, name='upload_products_csv'),
    path('product/<uuid:pk>/', product_update_delete, name='product_update_delete'),
    path('products/', list_products, name='list_products'),
]