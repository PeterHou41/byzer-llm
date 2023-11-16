from . import code_utils
import json
from typing import Any, TypeVar, Dict,Union,List
from byzerllm.utils.client import LLMRequest,LLMRequestExtra

def is_summary(data_analysis,prompt:str,role_mapping:Dict[str,str])->bool:
    p = f'''
Please check the following question is whether about summary:

```
{prompt}                               
```                               

If the question is about summary, please output the following json format:

```json
{{"is_summary":true}}
```

otherwise, output the following json format:

```json 
{{"is_summary":false}}
```
'''
    v = data_analysis.llm.chat(None, request=LLMRequest(instruction=p,extra_params=LLMRequestExtra(**role_mapping)))[0].output
    is_summary = True
    responses = code_utils.extract_code(v)
    for lang,code in responses:
        if lang == "json":
            try:
                is_summary = json.loads(code)["is_summary"]
            except Exception as inst:
                pass 
    return is_summary
    

def is_visualization(data_analysis,prompt:str,role_mapping:Dict[str,str])->bool:
    p = f'''
Please check the following question is whether about data visualization:

```text
{prompt}                               
```   

If the question is about data visualization, please output the following json format:

```json
{{"is_visualization":true}}
```

otherwise, output the following json format:

```json 
{{"is_visualization":false}}
```
'''
    
    v = data_analysis.llm.chat(None, request=LLMRequest(instruction=p,extra_params=LLMRequestExtra(**role_mapping)))[0].output
    is_visualization = True
    responses = code_utils.extract_code(v)
    for lang,code in responses:
        if lang == "json":
            try:
                is_visualization = json.loads(code)["is_visualization"]
            except Exception as inst:
                pass 
    return is_visualization 

def should_generate_code_to_response(data_analysis,prompt:str,role_mapping:Dict[str,str]):    
    preview_csv = data_analysis.file_preview.to_csv(index=False) 
    p = f'''I have a file the path is /home/byzerllm/projects/jupyter-workspace/test.csv, 
The preview of the file is:
```text
{preview_csv}
```
you should try to check the following quesion is whether need to generate python code to answer.

The question is:
                                            
```text
{prompt}
```

if you need to generate python code to answer, please output the following json format:

```json
{{"need_code":true}}
```

otherwise, output the following json format:

```json 
{{"need_code":false}}
```
'''       
    v = data_analysis.llm.chat(None,request=LLMRequest(instruction=p,extra_params=LLMRequestExtra(**role_mapping)))[0].output
    need_code = True
    responses = code_utils.extract_code(v)
    for lang,code in responses:
        if lang == "json":
            try:
                need_code = json.loads(code)["need_code"]
            except Exception as inst:
                pass 
    return need_code        