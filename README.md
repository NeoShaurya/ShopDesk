# ShopDesk

> Complete offline billing, inventory, GST invoicing, and accounting desktop application for retail & wholesale stores.

## 📥 Download & Install (Windows)

Download the ready-to-run Windows installer:
* **[Download ShopDesk Setup v0.0.4 (`ShopDesk-Setup-0.0.4.exe`)](releases/ShopDesk-Setup-0.0.4.exe)**

Run the installer and launch ShopDesk directly from your desktop or start menu. No Python or command-line setup required!

---

## First-time setup
Open **Settings**: enter shop name/address/phone/GSTIN, choose the bill paper
(A4, Thermal 80 mm, Thermal 58 mm), and tick "prices include GST" if that is how you price.
Use "Preview a sample bill" to check the layout.

## Daily use
- **Billing**: scan a barcode or type a product and press Enter, then Save & Print.
- **Stock In / Out**: "Receive stock" when a supplier delivers (batch + expiry + prices); "Adjust / write off" for damaged or expired items.
- **Customers & Suppliers**: contacts, GSTIN, who owes what, and "Record payment".
- **GSTIN**: your shop GSTIN (Settings) prints on every bill, headed TAX INVOICE. Customer GSTIN prints when saved on the customer. HSN codes are set per product in Inventory.

## Newer screens
- **Purchases**: supplier invoice with GST, HSN, batch, expiry. Adds stock and what you owe the supplier.
- **Returns**: customer returns (credit note) and supplier returns (debit note), all printable.
- **Pricing & Offers**: price lists (Retail / Wholesale / MRP / your own), fixed prices per product, and buy-X-get-Y-free schemes.
  Assign a price list and a trade discount to each customer (Customers & Suppliers > Edit).
- **Ledger**: select a customer or supplier and press Ledger for a printable statement.
- **Reports > Reorder suggestion**: what to order, based on reorder level and the last 30 days of sales.
- **Backup database**: button at the bottom of the sidebar. Pick a USB drive or a Google Drive folder.

## GST, records and tools
- **GST Reports**: pick a month and year, press Generate GSTR-1 or Generate GSTR-3B, check the preview, then Print, Save PDF,
  Export CSV (one file per table, portal-style columns) or Export Excel. Set each customer's / supplier's **State** (Edit) so
  sales and purchases to other states show as IGST. These are summaries to help prepare the return; check them with your CA.
- **Export to Tally**: choose dates and ledger names, then import the two XML files (Ledgers first, then Vouchers) in Tally.
- **Audit Log**: permanent record of what was created, changed or deleted (products, prices, bills, stock corrections, settings...). It cannot be edited.
- **Search** box at the top finds products, customers, suppliers, invoices and batches; click a result to open it.
- **Unsaved work** (a half-made bill or purchase) is saved every 30 seconds; after a crash or power cut you are asked whether to restore it.
- **Settings**: dark mode, and keyboard shortcuts you can change.
- Product GST rates come from a list you control (Settings > GST rates...), HSN codes must be 4, 6 or 8 digits, and a bill priced below cost asks for confirmation.

## Accounting and Marg-style features
- **GST rates** (Settings > GST rates...): edit the allowed rates, or move every product from one rate to another (optionally keeping
  the final price the same). Old bills never change.
- **PDC Register**: post-dated cheques received / issued (pending, deposited, cleared, bounced). Clearing posts the payment; bounced
  leaves the balance as it was. A reminder appears once a day for cheques that have reached their date, and Home shows "PDCs maturing this week".
- **Financial Years**: every entry is tagged with its year. Closing a year makes it read-only; reopening is possible. A new year starts
  with the balances carried forward. Every report has a financial-year picker.
- **Freeze Entries**: a frozen entry (bill, purchase, return, payment, expense) shows a lock and can never be edited or deleted. Freezing
  cannot be undone, and each freeze is written to the Audit Log.
- **Cheque printing**: Print cheque on supplier payments and PDC entries. HDFC, SBI and ICICI layouts, amount in words and figures, preview,
  and adjustable layouts (Cheque layouts...). **Test with a plain paper copy held against a real cheque and nudge the layout before using real cheques.**
- **Expenses**: rent, salaries etc. Needed for the cash book, bank book and profit & loss. Enter opening cash and bank balance once in Settings.
- **Reports**: day book, cash book, bank book, customer / supplier ledger statements, outstanding receivables and payables (with ageing),
  stock valuation (also batch-wise), profit & loss statement, balance sheet, GST sales tax summary, purchase register, item-wise sales and
  purchases, cancelled bills, foreign currency transactions. Cash book = cash; bank book = UPI, card, cheque and transfers.
  The balance sheet has no separate capital account: owner's capital is the balancing figure. Stock is valued at cost.
- **Invoice series** (Settings > Invoice series...): e.g. RET-001 and WHO-001; pick the series on the Billing screen. GSTR-1 lists each series separately.
- **Cancel bill**: open a bill (search, or double-click it in Reports) and press Cancel bill. Stock goes back, the customer's balance is reduced,
  the bill disappears from reports and GST, the number is not reused, and the reason is in the Audit Log. Money already paid for it is not tracked: hand it back.
- **Reprint last bill**: button on the Billing screen.
- **Credit limit**: set per customer; Settings chooses whether going over only warns or blocks the bill.
- **Reorder levels by location**: Stock In / Out > Godowns > Reorder levels...; alerts appear on Home.
- **Price history**: select a product in Inventory and press Price history. **Print labels**: barcode labels (Code 128) on A4 sheets or a label roll.
  Products without a barcode get one automatically (SD000012).
- **Currencies** (Settings > Currencies...): all amounts are stored in the base currency (INR). A bill, purchase or payment in another currency
  stores the currency and that day's rate, and the bill shows both amounts. Prices on bills are entered in INR; on purchases the cost is typed in the invoice's currency.
  Changing a rate later never changes old documents. Forex gain / loss between bill rate and payment rate is not calculated.

## Keyboard
- **Enter** moves to the next box in every form (on the last box of a dialog it saves). **Up / Down** move between text boxes; **Left / Right** move
  when the cursor is at the start / end of the text. Number, date and drop-down boxes keep Up / Down for changing their value.
  Barcode, product and search boxes keep Enter for their own job (scan, add, search).

## Printing
- Bills: after Save, the bill opens with Print / Preview / Save PDF (send the PDF on WhatsApp).
  "Save & Print" opens the Windows print dialog straight away.
- Reports: every report has Print / Preview / Save PDF, and prints exactly what is on screen.
- Thermal printers: in the print dialog pick your receipt printer. The bill page is cut to the
  bill's exact length, so no wasted paper.

## Making the installer (ShopDesk-Setup.exe)
On any Windows 10/11 PC with internet, once:
1. Install **Python 3.12** from python.org (tick "Add python.exe to PATH" on the first screen).
2. Install the free **Inno Setup 6** from jrsoftware.org/isdl.php (the build file tries to install it for you with winget).
3. Double-click **BUILD_INSTALLER.bat**. After a few minutes you get `installer_output\ShopDesk-Setup-1.0.0.exe`.

Give that one file to anyone: it installs ShopDesk for the current user (no administrator needed), adds a Start menu entry and an
optional desktop shortcut with the ShopDesk icon, and has a normal uninstaller. Installing a newer version over an old one keeps the data.

**Where the data lives:** the installed program keeps the shop's data in `C:\Users\<name>\AppData\Local\ShopDesk\data\shop.db`
(paste `%LOCALAPPDATA%\ShopDesk\data` into the Explorer address bar). Uninstalling does not delete it. If you used the unzipped version before,
close it, copy its `data\shop.db` into that folder once, and start the installed ShopDesk. Keep taking backups (button in the sidebar).
