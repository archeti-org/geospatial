# -*- encoding: utf-8 -*-
##############################################################################
#
#    Author Nicolas Bessi. Copyright Camptocamp SA
##############################################################################
from shapely.wkb import dumps as wkbdumps, loads as  wkbloads
from shapely.wkt import dumps as wktdumps, loads as  wktloads
from shapely.geometry import asShape
import logging

UNION_MAPPING = {'|': 'OR', '&':'AND'}

logger = logging.getLogger('GeoEngine sql debug')

# TODO Refactor geo_search and dry up the get_**_sql code

def _get_geo_func(model, domain):
    current_field = model._columns[domain[0]]
    current_operator = current_field._geo_operator
    attr = "get_%s_sql" % (domain[1],)
    if not hasattr(current_operator, attr):
        raise ValueError('Field %s does not support %s' %(current_field, domain[1]))
    func = getattr(current_operator, attr)
    return func

def geo_search(model, cursor, uid, domain=[], geo_domain=[], offset=0, limit=None, order=None, context=None):
    context = context or {}
    model.pool.get('ir.model.access').check(cursor, uid, model._name, 'read')
    query = model._where_calc(cursor, uid, domain, active_test=True, context=context)
    model._apply_ir_rules(cursor, uid, query, 'read', context=context)
    order_by = model._generate_order_by(order, query)
    from_clause, where_clause, where_clause_params = query.get_sql()
    limit_str = limit and ' limit %d' % limit or ''
    offset_str = offset and ' offset %d' % offset or ''
    where_clause_arr= []
    if where_clause and where_clause_params:
        where_clause_arr.append(where_clause)
    #geosearch where clause generation
    MODE = ''
    UNION = 'AND'
    JOIN_MODE = '%s %s'
    for domain in geo_domain:
        if isinstance(domain, basestring):
            if domain == '!':
                MODE = 'NOT'
            if domain in UNION_MAPPING.keys():
                UNION = UNION_MAPPING[domain]
        if where_clause_arr:
            where_clause_arr.append(JOIN_MODE % (MODE, UNION))
        # We start computing geo spation SQL
        if isinstance(domain, (list, tuple)):
            if isinstance(domain[2], dict):
                # We are having indirect geo_operator like (‘geom’, ‘geo_...’, {‘res.zip.poly’: [‘ids’, ‘in’, [1,2,3]] })
                ref_search = domain[2]
                rel_where_statement = []
                for key in ref_search:
                    i = key.rfind('.')
                    rel_model = key[0:i]
                    rel_col = key[i+1:]
                    rel_model = model.pool.get(rel_model)
                    from_clause += ', %s' % (rel_model._table,)
                    att_where_sql = u''
                    # we compute the attributes search on spatial rel
                    if ref_search[key]:
                        rel_query = rel_model._where_calc(cursor, uid, ref_search[key],
                                                      active_test=True, context=context)
                        rel_res = rel_query.get_sql()
                        att_where_sql = rel_res[1]
                        where_clause_params += rel_res[2]
                    # we compute the spatial search on spatial rel
                    func = _get_geo_func(model, domain)
                    spatial_where_sql = func(model._table, domain[0], domain[2], 
                                             rel_col=rel_col, rel_model=rel_model)
                    if att_where_sql:
                        rel_where_statement.append(u"(%s AND %s)" % (att_where_sql, spatial_where_sql))
                    else:
                        rel_where_statement.append(u"(%s)" % (spatial_where_sql))
                where_clause_arr.append(u"AND ".join(rel_where_statement))
            else:
                current_field = model._columns[domain[0]]
                func = _get_geo_func(model, domain)
                where_sql = func(model._table, domain[0], domain[2])
                where_clause_arr.append(where_sql)
    if where_clause_arr:
        where_statement =  " WHERE %s" % (u' '.join(where_clause_arr))
    else:
        where_statement = u''
    sql= 'SELECT "%s".id FROM ' % model._table + from_clause + where_statement + order_by or '' + limit_str or '' + offset_str or ''
    print cursor.mogrify(sql, where_clause_params)
    logger.debug(cursor.mogrify(sql, where_clause_params))
    cursor.execute(sql, where_clause_params)
    res = cursor.fetchall()
    if res :
        return [x[0] for x in res]
    else:
        return []
                
class GeoOperator(object):
    
    def __init__(self, geo_field):
        self.geo_field = geo_field
        
    def get_rel_field(self, rel_col, rel_model):
        """Retriev the expression to use in PostGIS statement for a spatial
           rel search"""
        try:
            rel_model._columns[rel_col]
        except Exception, exc:
            raise Exception('Model %s has no column %s' % (rel_model._name, rel_col))
        return "%s.%s" %(rel_model._table, rel_col)
        
    def _get_direct_como_op_sql(self, table, col, value, rel_col=None, rel_model=None, op=''):
        "provide raw sql for geater and lesser operators"
        if isinstance(value, (int, long, float)):
            if  rel_col and rel_model:
                raise Exception('Area %s does not support int compare for relation search' % (op,))
            return " ST_Area(%s.%s) %s %s" %(table, col, op, value)
        else:
            if rel_col and rel_model:
               compare_to = self.get_rel_field(rel_col, rel_model)
            else:   
                base = self.geo_field.entry_to_shape(value, same_type=False)
                compare_to = base.wkt
            return " ST_Area(%s.%s) %s ST_Area(ST_GeomFromText('%s'))" %(table, col, op, compare_to)
            
    def _get_postgis_comp_sql(self, table, col, value, rel_col=None, rel_model=None, op=''):
        "return raw sql for all search based on St_**(a, b) posgis operator"
        if rel_col and rel_model:
           compare_to = self.get_rel_field(rel_col, rel_model)
        else:
            base = self.geo_field.entry_to_shape(value, same_type=False)
            compare_to = "ST_GeomFromText('%s')" %(base.wkt,)
        return " %s(%s.%s, %s)" %(op, table, col, compare_to)

    ## Area comparison #############        
    def get_geo_greater_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_greater operator"
        return self._get_direct_como_op_sql(table, col, value, rel_col=rel_col, rel_model=rel_model, op='>')
    
    def get_geo_lesser_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_lesser operator"
        return self._get_direct_como_op_sql(table, col, value, rel_col=rel_col, rel_model=rel_model, op='>')
        
    ## Equality comparison #############        

    def get_geo_equal_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_equal operator"
        if rel_col and rel_model:
           compare_to = self.get_rel_field(rel_col, rel_model)
        else:
            base = self.geo_field.entry_to_shape(value, same_type=False)
            compare_to = "ST_GeomFromText('%s')" %(base.wkt,)
        return " %.%s = %s" %(table, col, compare_to)
        
    ## PostGis spatial comparison ###########    
        
    def get_geo_intersect_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_intersec operator"
        return self._get_postgis_comp_sql(table, col, value, rel_col= rel_col, rel_model= rel_model, op='ST_Intersects')
        
    def get_geo_touch_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_touch operator"
        return self._get_postgis_comp_sql(table, col, value, rel_col= rel_col, rel_model= rel_model, op='ST_Touches')
        
    def get_geo_within_sql(self, table, col, value, rel_col=None, rel_model=None):
        "return raw sql for geo_within operator"
        return self._get_postgis_comp_sql(table, col, value, rel_col= rel_col, rel_model= rel_model, op='ST_Within')
