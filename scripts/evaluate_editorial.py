"""Prepare or run isolated editorial comparisons; live runs are explicitly bounded.

Dry-run (default) never calls providers. --live --case ID compares real outputs,
records tokens/time and creates a human review sheet without assigning fake scores.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, generation, pipeline, youtube
from app.editorial import source_processing, store, text_checks
from app.editorial.contracts import VoiceProfile
from app.schemas import Brief
from app.seo import knowledge
from app.security import get_secret


def source_job(case):
    sources=[]
    for index,text in enumerate(case['videos'],1):
        text=(text+'\n')*case.get('repeat',1)
        if index==len(case['videos']):text+='\n'+case.get('tail','')
        vid=f'eval{index:07d}'
        sources.append({'id':f'v{index}','video_id':vid,'url':'https://www.youtube.com/watch?v='+vid,
                        'title':f'Fonte sintética {index}','author':f'Origem {index}','thumbnail':'',
                        'provider':'Gabarito sintético editorial','language':'pt','status':'ok',
                        'generated_captions':None,'segments':youtube.manual_segments(text,f'v{index}')})
    brief=Brief(**{'topic':case['question'],'main_question':case['question'],
                'audience':'Leitores que não assistiram aos vídeos','genre':'explicação','target_words':800,
                **case.get('brief',{}),'urls':[s['url'] for s in sources],'research':False}).model_dump()
    return {'id':uuid.uuid4().hex,'created_at':db.now(),'status':'sources_ready','brief':brief,
            'sources':sources,'article':None,'review':None,'events':[],'usage':[]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=ROOT/'tests/evaluation/editorial_cases.json')
    parser.add_argument('--output',type=Path,default=ROOT/'.local/editorial-evaluation')
    parser.add_argument('--case')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--flow',choices=['video_first'],default='video_first')
    parser.add_argument('--model')
    parser.add_argument('--max-calls',type=int,choices=range(4,25),default=24)
    parser.add_argument('--context-chars',type=int,default=90000)
    parser.add_argument('--composition',choices=['coherent'],default='coherent')
    args=parser.parse_args()
    suite=json.loads(args.suite.read_text(encoding='utf-8'))
    cases=[c for c in suite['cases'] if not args.case or c['id']==args.case]
    if not cases:parser.error('Caso não encontrado no conjunto de avaliação.')
    if args.live and not args.case:parser.error('--live exige --case para delimitar o consumo desta execução.')
    profile=VoiceProfile(max_calls=args.max_calls,max_rounds=0,context_chars=args.context_chars).model_dump()
    key=get_secret('openai_api_key') if args.live else None
    model=args.model or (db.get_setting('model','gpt-4.1-mini') if args.live else 'configured-on-live-run')
    if args.live and not key:parser.error('Configure OPENAI_API_KEY ou a integração local antes de executar --live.')
    output=args.output/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    output.mkdir(parents=True,exist_ok=False)
    run={'suite_version':suite['version'],'live':args.live,'model':model,'max_calls_per_cycle':args.max_calls,
         'composition':args.composition,
         'cases':[], 'human_review_required':True}
    old_dir=os.environ.get('DATA_DIR');old_flow=os.environ.get('EDITORIAL_FLOW');old_key=os.environ.get('OPENAI_API_KEY')
    old_composition=os.environ.get('EDITORIAL_COMPOSITION')
    try:
        os.environ['DATA_DIR']=str(output/'isolated-data')
        os.environ['EDITORIAL_COMPOSITION']=args.composition
        db.init();store.init();knowledge.init();db.set_setting('editorial_profile',profile)
        db.set_setting('model',model)
        if key:
            os.environ['OPENAI_API_KEY']=key
        for case in cases:
            for key in ('brand_name','brand_voice'):
                db.set_setting(key,case.get('brand',{}).get(key,''))
            job=source_job(case)
            estimate=source_processing.estimate(job,profile)
            entry={'id':case['id'],'gold':case['gold'],'estimate':estimate,'outputs':{},
                   'human_review':{'reviewer':None,'missing_facts':None,'unsupported_claims':None,
                       'attribution_errors':None,'lost_conditions':None,'conflict_handling':None,
                       'paragraph_context':None,'beginning_middle_end':None,'required_edits':None,'notes':None}}
            if args.live:
                for flow in [args.flow]:
                    current=deepcopy(job);current['id']=uuid.uuid4().hex
                    os.environ['EDITORIAL_FLOW']=flow;db.save_job(current)
                    started=time.monotonic();pipeline.run(current['id'])
                    result=db.get_job(current['id'])
                    metrics={'status':result['status'],'error':result.get('error'),
                             'seconds':round(time.monotonic()-started,2),
                             'calls':result.get('editorial',{}).get('calls',0),
                             'input_tokens':sum(u['input_tokens'] for u in result['usage'])
                                 if result['usage'] and all(type(u.get('input_tokens')) is int for u in result['usage']) else None,
                             'output_tokens':sum(u['output_tokens'] for u in result['usage'])
                                 if result['usage'] and all(type(u.get('output_tokens')) is int for u in result['usage']) else None,
                             'delivery':text_checks.analyze(result) if result.get('article') else None}
                    entry['outputs'][flow]=metrics
                    (output/f'{case["id"]}-{flow}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                    if result.get('article'):
                        (output/f'{case["id"]}-{flow}.md').write_text('# '+result['article']['title']+'\n\n'+result['article']['markdown'],encoding='utf-8')
            run['cases'].append(entry)
        (output/'review.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'live':args.live,'cases':len(cases),'report':str(output/'review.json'),
                          'human_review_required':True},ensure_ascii=False))
    finally:
        for name,value in [('DATA_DIR',old_dir),('EDITORIAL_FLOW',old_flow),('OPENAI_API_KEY',old_key),
                           ('EDITORIAL_COMPOSITION',old_composition)]:
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    return 0


if __name__=='__main__':raise SystemExit(main())
