import re

with open('/Users/arshadaman/Cenvoras/frontend/cenvoras/src/components/invoice/InvoicePreview.jsx', 'r') as f:
    text = f.read()

text = text.replace(
    """  const grandTotal = subtotal + taxTotal;""",
    """  const roundOff = parseFloat(invoice.round_off || 0);
  const grandTotal = subtotal + taxTotal + roundOff;"""
)

# Insert the round_off row into the table before the TOTAL row
text = text.replace(
    """              <tr style={totalRowStyle}>
                <td className="px-3 py-3 border font-bold">
                  TOTAL""",
    """              {roundOff !== 0 && (
                <tr>
                  <td className="px-3 py-2 border font-medium" style={{ borderColor: colors.tableBorder }}>
                    Round Off
                  </td>
                  <td className="px-3 py-2 border text-right" style={{ borderColor: colors.tableBorder }}>
                    ₹{roundOff.toFixed(2)}
                  </td>
                </tr>
              )}
              <tr style={totalRowStyle}>
                <td className="px-3 py-3 border font-bold">
                  TOTAL"""
)

with open('/Users/arshadaman/Cenvoras/frontend/cenvoras/src/components/invoice/InvoicePreview.jsx', 'w') as f:
    f.write(text)
