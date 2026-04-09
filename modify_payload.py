import re

with open('/Users/arshadaman/Cenvoras/frontend/cenvoras/src/components/sales/SalesForm.jsx', 'r') as f:
    text = f.read()

text = text.replace(
    """              const totalAmount = processedItems.reduce((sum, item) => sum + item.amount, 0);
              const finalTotal = roundOffApplied ? computeRoundedTotal(totalAmount) : totalAmount;

              const formData = {""",
    """              const totalAmount = processedItems.reduce((sum, item) => sum + item.amount, 0);
              const finalTotal = roundOffApplied ? computeRoundedTotal(totalAmount) : totalAmount;
              const roundOffDelta = roundOffApplied ? Number((finalTotal - totalAmount).toFixed(2)) : 0;

              const formData = {
                round_off: roundOffDelta.toString(),"""
)

with open('/Users/arshadaman/Cenvoras/frontend/cenvoras/src/components/sales/SalesForm.jsx', 'w') as f:
    f.write(text)
