"""Exact parent/child mapping shared by discovery, precheck and readback.

No community predictions are copied into snapshots. The selected transport view
retains only the selected child's own-history container for reconciliation.
"""
from copy import deepcopy

VERSION = 'question_mapping_v1'


def question_ids(post, *, allow_groups=False):
    valid = lambda x: type(x) is int and x > 0
    if not isinstance(post, dict) or not valid(post.get('id')):
        raise ValueError('invalid post identity')
    shapes = [k for k in ('question','group_of_questions','conditional','notebook') if post.get(k) is not None]
    if shapes == ['question'] and isinstance(post['question'], dict):
        children = [post['question']]
    elif allow_groups and shapes == ['group_of_questions'] and isinstance(post['group_of_questions'], dict):
        group = post['group_of_questions']
        if not valid(group.get('id')):
            raise ValueError('invalid group identity')
        children = group.get('questions')
    else:
        raise ValueError('unsupported or ambiguous post structure')
    if not isinstance(children,list) or not children:
        raise ValueError('missing question children')
    if any(not isinstance(q,dict) or not valid(q.get('id')) or
           (q.get('post_id') is not None and q['post_id'] != post['id']) for q in children):
        raise ValueError('invalid child mapping')
    ids = [q['id'] for q in children]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate child identity')
    return ids


def select_question(post, question_id, *, allow_groups=False):
    if type(question_id) is not int or question_id not in question_ids(post,allow_groups=allow_groups):
        raise ValueError('selected child missing from post')
    group = post.get('group_of_questions')
    if group is None:
        return post
    q = next(q for q in group['questions'] if q['id']==question_id)
    result = {k:v for k,v in post.items() if k != 'group_of_questions'}
    child = deepcopy(q)
    if not isinstance(q.get('title'),str) or not q['title'].strip() or not isinstance(post.get('title'),str):
        raise ValueError('missing group or child title')
    result['title'] = post['title'] + ' — ' + q['title']
    for key in ('description','resolution_criteria','fine_print'):
        common, specific = group.get(key), q.get(key)
        if any(v is not None and not isinstance(v,str) for v in (common,specific)):
            raise ValueError('unrecognized child content')
        child[key] = '\n\n'.join(dict.fromkeys(v for v in (common,specific) if v)) or None
    # A group's parent can remain open after this child resolves. The exact
    # selected child's state is authoritative for submission, closure and scoring.
    result['status'] = child.get('status')
    result['resolved'] = child.get('status') == 'resolved'
    for key in ('open_time','scheduled_close_time','scheduled_resolve_time','actual_close_time','actual_resolve_time'):
        result[key] = child.get(key)
    result['question'] = child
    result['_group_mapping'] = {'post_type':'group_of_questions','group_id':group['id'],
                                 'mapping_version':VERSION}
    return result
