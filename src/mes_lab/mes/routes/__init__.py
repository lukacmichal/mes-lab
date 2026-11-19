"""The routes of the MES, one module per part of it; each has register(app, m) (m: what they share, see
..context.Mes). The order is the order of the registration."""

from . import auth, connect, erp, labels, pages, people, plant, quality, settings, shop_floor, training, users, views

ALL = (shop_floor, auth, views, quality, people, plant, connect, training, erp, labels, settings, users, pages)
