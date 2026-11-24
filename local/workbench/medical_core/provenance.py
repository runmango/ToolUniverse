"""Missing historical model metadata is explicit, never guessed."""
import copy

def generation(record, parent=None):
    parent=parent or {}
    source=str(record.get('annotation_source',parent.get('annotation_source','imported'))).strip().casefold()
    raw=record.get('generation',parent.get('generation'))
    if raw is None and source not in {'ai','model','synthetic','rule'}: return {}
    if raw is not None and not isinstance(raw,dict): raise ValueError('generation 必须是 JSON 对象')
    data=copy.deepcopy(raw or {})
    kind=data.get('origin',source)
    data.update(origin=kind,model_name=str(data.get('model_name') or 'unknown'),
                model_version=str(data.get('model_version') or 'unknown'),
                verification_status='unverified')
    data['metadata_status']='complete' if data['model_name']!='unknown' and data['model_version']!='unknown' else 'incomplete'
    return {'generation':data}
