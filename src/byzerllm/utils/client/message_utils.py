from typing import List, Dict,Any

def padding_meesages_merge(data:List[Dict[str,Any]]):
    '''
    merge the neighbor messages with the same role
    '''
    padded_data = []
    last_role = None
    last_message = None
    for message in data:
        if last_role is None:
            padded_data.append(message)
        elif last_role == message['role']:
            last_message['content'] += message['content']
        else:
            padded_data.append(message)
        last_role = message['role']
        last_message = message
    return padded_data

def padding_meesages_expand(data:Dict[str,Any]):
    '''
    padding the message between the neighbor messages with the same role
    '''
    padded_data = []        
    last_role = None                
    for message in data:            
        if (last_role is None) and (message['role'] == 'assistant'):
            padded_data.append({'content': 'continue', 'role': 'user'})
            padded_data.append(message)

        elif (last_role is None) and (message['role'] == 'user'):                
            padded_data.append(message)    

        elif (last_role == message['role']) and (message['role'] == 'assistant'):
            padded_data.append({'content': 'continue', 'role': 'user'})
            padded_data.append(message)

        elif (last_role == message['role']) and (message['role'] == 'user'):
            padded_data.append({'content': 'continue', 'role': 'assistant'})
            padded_data.append(message)

        elif (last_role == message['role']) and (message['role'] == 'user'):                                        
            padded_data.append(message)

        else:
            padded_data.append(message)    
        
        last_role = message['role']
    
    if last_role == 'assistant':
        padded_data.append({'content': 'continue', 'role': 'user'})

    return padded_data