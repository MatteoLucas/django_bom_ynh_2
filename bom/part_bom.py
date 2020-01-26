from .base_classes import AsDictModel
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)


class PartBom(AsDictModel):
    def __init__(self, part_revision, quantity, unit_cost=0, missing_item_costs=0, nre=0, out_of_pocket_cost=0):
        self.part_revision = part_revision
        self.parts = OrderedDict()
        self.quantity = quantity

        self.unit_cost = unit_cost
        self.missing_item_costs = missing_item_costs  # count of items that have no cost
        self.nre = nre
        self.out_of_pocket_cost = out_of_pocket_cost  # cost of buying self.quantity with MOQs

    def cost(self):
        return float(self.unit_cost) * self.quantity

    def total_out_of_pocket_cost(self):
        return float(self.out_of_pocket_cost) + float(self.nre)

    def append_item_and_update(self, item):
        if item.bom_id in self.parts:
            self.parts[item.bom_id].extended_quantity += item.extended_quantity
            ref = ', ' + item.references
            self.parts[item.bom_id].references += ref
        else:
            self.parts[item.bom_id] = item

            item.total_extended_quantity = int(self.quantity) * item.extended_quantity
            self.update_bom_for_part(item)

    def update_bom_for_part(self, bom_part):
        if bom_part.seller_part:
            try:
                bom_part.order_quantity = bom_part.seller_part.order_quantity(bom_part.total_extended_quantity)
                bom_part.order_cost = bom_part.total_extended_quantity * bom_part.seller_part.unit_cost
            except AttributeError:
                pass

            self.unit_cost = (self.unit_cost + float(bom_part.seller_part.unit_cost) * bom_part.extended_quantity) if bom_part.seller_part.unit_cost is not None else self.unit_cost
            self.out_of_pocket_cost = self.out_of_pocket_cost + bom_part.out_of_pocket_cost()
            self.nre = (self.nre + bom_part.seller_part.nre_cost) if bom_part.seller_part.nre_cost is not None else self.nre
        else:
            self.missing_item_costs += 1

    def update(self):
        self.missing_item_costs = 0
        self.unit_cost = 0
        self.out_of_pocket_cost = 0
        self.nre = 0
        for _, bom_part in self.parts.items():
            self.update_bom_for_part(bom_part)

    def mouser_parts(self):
        mouser_items = {}
        for bom_id, item in self.parts.items():
            if item.part.id not in mouser_items:
                for manufacturer_part in item.part.manufacturer_parts():
                    mouser_items.update({bom_id: manufacturer_part})
                    # if manufacturer_part.source_mouser:
                    #     mouser_items.update({bom_id: manufacturer_part})
        return mouser_items

    def manufacturer_parts(self, source_mouser=False):
        # TODO: optimize this query to not hit the DB in a for loop
        if source_mouser:
            mps = []
            for item in self.parts:
                if item.part.manufacturer_part.source_mouser:
                    mps.append(item.part.manufacturer_part)
            return mps
        return [item.part.manufacturer_part for item in self.parts]


class PartBomItem(AsDictModel):
    def __init__(self, bom_id, part, part_revision, do_not_load, references, quantity, extended_quantity, seller_part=None):
        # top_level_quantity is the highest quantity, typically a order quantity for the highest assembly level in a BOM
        # A bom item should not care about its parent quantity
        self.bom_id = bom_id
        self.part = part
        self.part_revision = part_revision
        self.do_not_load = do_not_load
        self.references = references

        self.quantity = quantity  # quantity is the quantity per each direct parent assembly
        self.extended_quantity = extended_quantity  # extended_quantity, is the item quantity used in the top level assembly (e.g. assuming PartBom.quantity = 1)
        self.total_extended_quantity = None  # extended_quantity * top_level_quantity (PartBom.quantity) - Set when appending to PartBom
        self.order_quantity = None  # order quantity taking into MOQ/MPQ constraints - Set when appending to PartBom

        self.order_cost = 0  # order_cost is updated similar to above order_quantity - Set when appending to PartBom
        self.seller_part = seller_part

        self.api_info = None

    def extended_cost(self):
        try:
            return self.extended_quantity * float(self.seller_part.unit_cost)
        except (AttributeError, TypeError) as err:
            logger.log(logging.INFO, '[part_bom.py] ' + str(err))
            return 0

    def out_of_pocket_cost(self):
        try:
            return self.order_quantity * float(self.seller_part.unit_cost)
        except (AttributeError, TypeError) as err:
            logger.log(logging.INFO, '[part_bom.py] ' + str(err))
            return 0

    def as_dict(self, include_id=False):
        dict = super().as_dict()
        del dict['bom_id']
        return dict

    def as_dict_for_export(self):
        return {
            'part_number': self.part.full_part_number(),
            'quantity': self.quantity,
            'do_not_load': self.do_not_load,
            'part_class': self.part.number_class.name,
            'references': self.references,
            'part_synopsis': self.part_revision.synopsis(),
            'part_revision': self.part_revision.revision,
            'part_manufacturer': self.part.primary_manufacturer_part.manufacturer.name if self.part.primary_manufacturer_part is not None and self.part.primary_manufacturer_part.manufacturer is not None else '',
            'part_manufacturer_part_number': self.part.primary_manufacturer_part.manufacturer_part_number if self.part.primary_manufacturer_part is not None else '',
            'part_ext_qty': self.extended_quantity,
            'part_order_qty': self.order_quantity,
            'part_seller': self.seller_part.seller.name if self.seller_part is not None else '',
            'part_cost': self.seller_part.unit_cost if self.seller_part is not None else '',
            'part_moq': self.seller_part.minimum_order_quantity if self.seller_part is not None else 0,
            'part_ext_cost': self.extended_cost(),
            'part_out_of_pocket_cost': self.out_of_pocket_cost(),
            'part_nre': self.seller_part.nre_cost if self.seller_part is not None else 0,
            'part_lead_time_days': self.seller_part.lead_time_days if self.seller_part is not None else 0,
        }


class PartIndentedBomItem(PartBomItem, AsDictModel):
    def __init__(self, indent_level, parent_id, subpart, parent_quantity, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.indent_level = indent_level
        self.parent_id = parent_id
        self.subpart = subpart
        self.parent_quantity = parent_quantity

    def as_dict_for_export(self):
        dict = super().as_dict_for_export()
        dict.update({
            'level': self.indent_level,
        })
        return dict