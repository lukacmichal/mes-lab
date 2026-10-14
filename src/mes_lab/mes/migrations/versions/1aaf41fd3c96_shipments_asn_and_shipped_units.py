"""shipments (ASN) and shipped units

Revision ID: 1aaf41fd3c96
Revises: c4a979406352
Create Date: 2026-10-08 19:15:46.362501
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '1aaf41fd3c96'
down_revision = 'c4a979406352'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('shipment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asn_no', sa.String(length=30), nullable=False),
    sa.Column('customer', sa.String(length=20), nullable=False),
    sa.Column('plate', sa.String(length=20), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('departed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('pallets', sa.Integer(), nullable=False),
    sa.Column('klt', sa.Integer(), nullable=False),
    sa.Column('units', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('asn_no')
    )
    op.create_index(op.f('ix_shipment_departed_at'), 'shipment', ['departed_at'], unique=False)
    op.add_column('unit', sa.Column('shipment_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_unit_shipment_id'), 'unit', ['shipment_id'], unique=False)
    op.create_foreign_key('fk_unit_shipment_id', 'unit', 'shipment', ['shipment_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_unit_shipment_id', 'unit', type_='foreignkey')
    op.drop_index(op.f('ix_unit_shipment_id'), table_name='unit')
    op.drop_column('unit', 'shipment_id')
    op.drop_index(op.f('ix_shipment_departed_at'), table_name='shipment')
    op.drop_table('shipment')
