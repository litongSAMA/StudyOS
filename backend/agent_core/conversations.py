"""Scoped, persistent multi-turn context. LLM text cannot issue write actions."""
from uuid import UUID, uuid4
import json
import re
from .assistant import chat
from .common import AgentError, canonical, digest, text
from .deadlines import CHANGE_MARKER

SCHEMA='''
CREATE TABLE IF NOT EXISTS agent_conversations(id TEXT PRIMARY KEY,scope TEXT NOT NULL,messages TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_turns(conversation_id TEXT NOT NULL,turn_id TEXT NOT NULL,request_hash TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(conversation_id,turn_id),FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id) ON DELETE CASCADE);
'''


def uuid(value,field):
    try: return str(UUID(value))
    except (ValueError,TypeError,AttributeError): raise AgentError(422,f'{field} 必须是 UUID') from None


def get_conversation(service,conversation_id,scope):
    with service.db.snapshot() as con:
        row=con.execute('SELECT * FROM agent_conversations WHERE id=? AND scope=?',(conversation_id,scope)).fetchone()
        if not row: raise AgentError(404,'对话不存在或不属于当前账号')
        return {**{key:row[key] for key in ['id','revision','created_at','updated_at']},'messages':json.loads(row['messages'])}


def conversation_chat(service,data,scope):
    message=text(data.get('message'),'消息',10000,True)
    cid=uuid(data.get('conversation_id') or str(uuid4()),'conversation_id')
    tid=uuid(data.get('turn_id') or str(uuid4()),'turn_id')
    revision=data.get('expected_revision',0)
    if isinstance(revision,bool) or not isinstance(revision,int) or revision<0: raise AgentError(422,'对话版本无效')
    request_hash=digest(data)
    with service.db.transaction() as con:
        prior=con.execute('SELECT * FROM agent_turns WHERE conversation_id=? AND turn_id=?',(cid,tid)).fetchone()
        row=con.execute('SELECT * FROM agent_conversations WHERE id=?',(cid,)).fetchone()
        if row and row['scope']!=scope: raise AgentError(404,'对话不存在或不属于当前账号')
        if prior:
            if prior['request_hash']!=request_hash: raise AgentError(409,'相同对话请求编号不能用于不同内容')
            return {**json.loads(prior['response']),'turn_replayed':True}
        if row and row['revision']!=revision: raise AgentError(409,'对话已在其它页面更新，请重新读取对话后继续')
        if not row:
            if revision!=0: raise AgentError(409,'对话版本无效，请重新开始')
            con.execute('INSERT INTO agent_conversations VALUES (?,?,?,?,?,?,?)',(cid,scope,'[]','{}',0,service.now(),service.now()))
            row=con.execute('SELECT * FROM agent_conversations WHERE id=?',(cid,)).fetchone()
        history=json.loads(row['messages']);state=json.loads(row['state'])
    course=data.get('course')
    with service.db.snapshot() as con:
        named=[r['name'] for r in con.execute('SELECT name FROM courses') if r['name'] in message]
    if not course and len(named)==1: course=named[0]
    followup=bool(re.search(r'它|这个|这些|刚才|继续|再详细|那|再找|同一',message))
    if not course and followup: course=state.get('course')
    effective=message
    if re.search(r'再详细|展开|继续解释|(?:它|这个|这些).*(?:是什么|什么意思|为什么|怎么|解释|例子)',message) and state.get('query'):
        effective='找'+state['query']+'的资料'
    task_id=data.get('task_id')
    if task_id is None and CHANGE_MARKER.search(message) and followup: task_id=state.get('task_id')
    plan_id=data.get('plan_id')
    if plan_id is None and followup and re.search(r'重排|没完成|赶不上',message): plan_id=state.get('plan_id')
    response=chat(service,effective,course=course,task_id=task_id,plan_id=plan_id,new_due_at=data.get('new_due_at'),hours_per_day=data.get('hours_per_day',3),scope=scope)
    query=re.search(r'(?:搜索|查找|找一下|找|关于)\s*(.+?)(?:的资料|资料|课件|讲义|$)',effective)
    if query: state['query']=query[1].strip()
    if course: state['course']=course
    proposal=response.get('proposal',{})
    result=proposal.get('result',{})
    if result.get('task_id'): state['task_id']=result['task_id']
    if plan_id: state['plan_id']=plan_id
    if effective!=message and response['intent']=='learning_context':
        evidence=response.get('context',{}).get('materials',[])[:3]
        if evidence:
            response['reply']+='\n\n资料依据：\n'+'\n'.join('「'+item['title']+'」：'+item['snippet'] for item in evidence)
    response['conversation_id']=cid;response['revision']=revision+1;response['turn_id']=tid
    # Optional prose enhancer receives bounded source evidence. It cannot modify proposals.
    if service.answer and response['intent']=='learning_context':
        answer=service.answer(message,history[-8:],response.get('context',{}))
        if answer:
            response['reply']=answer;response['engine']='llm'
    history=(history+[{'role':'user','content':message},{'role':'assistant','content':response['reply']}])[-40:]
    with service.db.transaction() as con:
        prior=con.execute('SELECT response,request_hash FROM agent_turns WHERE conversation_id=? AND turn_id=?',(cid,tid)).fetchone()
        if prior:
            if prior['request_hash']!=request_hash: raise AgentError(409,'对话请求编号内容冲突')
            return {**json.loads(prior['response']),'turn_replayed':True}
        changed=con.execute('UPDATE agent_conversations SET messages=?,state=?,revision=?,updated_at=? WHERE id=? AND scope=? AND revision=?',(canonical(history),canonical(state),revision+1,service.now(),cid,scope,revision))
        if changed.rowcount!=1: raise AgentError(409,'对话已更新，请重新读取对话后继续')
        con.execute('INSERT INTO agent_turns VALUES (?,?,?,?)',(cid,tid,request_hash,canonical(response)))
    return response
