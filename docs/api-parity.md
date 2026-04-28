# API Parity

| Aruba section | Aruba endpoint | MCP tool | Implemented | Sensitive read | Binary |
|---|---|---|---:|---:|---:|
| Auth | `GET /auth/userInfo` | `aruba_get_user_info` | yes | no | no |
| Auth | `GET /auth/multicedenti` | `aruba_list_multicedenti` | yes | no | no |
| Auth | `GET /auth/multicedenti/{id}` | `aruba_get_multicedente_by_id` | yes | no | no |
| Sent invoices | `GET /services/invoice/out/findByUsername` | `aruba_find_sent_invoices` | yes | no | no |
| Sent invoices | `GET /services/invoice/out/getByFilename` | `aruba_get_sent_invoice_by_filename` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/getZipByFilename` | `aruba_get_sent_invoice_zip_by_filename` | yes | yes | yes |
| Sent invoices | `GET /services/invoice/out/{invoiceId}` | `aruba_get_sent_invoice_by_id` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/getByIdSdi` | `aruba_get_sent_invoice_by_sdi_id` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/pdd` | `aruba_get_sent_invoice_pdd` | yes | yes | yes |
| Received invoices | `GET /services/invoice/in/findByUsername` | `aruba_find_received_invoices` | yes | no | no |
| Received invoices | `GET /services/invoice/in/getByFilename` | `aruba_get_received_invoice_by_filename` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getZipByFilename` | `aruba_get_received_invoice_zip_by_filename` | yes | yes | yes |
| Received invoices | `GET /services/invoice/in/{invoiceId}` | `aruba_get_received_invoice_by_id` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getByIdSdi` | `aruba_get_received_invoice_by_sdi_id` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getInvoiceWithUnsignedFile` | `aruba_get_received_invoice_unsigned_file` | yes | yes | no |
| Received invoices | `GET /services/invoice/in/pdd` | `aruba_get_received_invoice_pdd` | yes | yes | yes |
| Sent notifications | `GET /services/notification/out/getByFilename` | `aruba_get_sent_notification_by_filename` | yes | yes | no |
| Sent notifications | `GET /services/notification/out/getByInvoiceFilename` | `aruba_get_sent_notifications_by_invoice_filename` | yes | yes | no |
| Sent notifications | `GET /services/notification/out/{invoiceId}` | `aruba_get_sent_notifications_by_invoice_id` | yes | yes | no |
| Received notifications | `GET /services/notification/in/getByFilename` | `aruba_get_received_notification_by_filename` | yes | yes | no |
| Received notifications | `GET /services/notification/in/getByInvoiceFilename` | `aruba_get_received_notifications_by_invoice_filename` | yes | yes | no |
| Received notifications | `GET /services/notification/in/{invoiceId}` | `aruba_get_received_notifications_by_invoice_id` | yes | yes | no |
| Customer result | `GET /services/invoice/in/sendEsitoCommittente/{filename}` | `aruba_get_customer_result_status` | yes | no | no |

Note: Aruba's received invoice PDD section is `GET /services/invoice/in/pdd`, while the English documentation example may show `out/pdd` in that received section. This implementation keeps the section path and documents the discrepancy.
