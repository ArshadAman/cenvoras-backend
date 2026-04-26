from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0009_webhookevent'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='webhookevent',
            old_name='subscription_event_i_8c3d9e_idx',
            new_name='sub_wh_ev_id_idx',
        ),
        migrations.RenameIndex(
            model_name='webhookevent',
            old_name='subscription_order_i_9f4a2c_idx',
            new_name='sub_wh_ord_id_idx',
        ),
        migrations.RenameIndex(
            model_name='webhookevent',
            old_name='subscription_process_5b1e7d_idx',
            new_name='sub_wh_proc_idx',
        ),
    ]
