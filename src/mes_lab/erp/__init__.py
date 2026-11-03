"""The fake ERP: the office system of the plant, and the interface it shares with the MES.

* `system` - the ERP itself: customer call-offs, MRP, production orders, confirmations, the stock in its books;
* `bus`    - the interface: messages with at-least-once delivery, retries, an error queue and idempotency.

The MES side of the interface is mes_lab.mes.erp_link. Settings: config/erp.yaml.
"""
