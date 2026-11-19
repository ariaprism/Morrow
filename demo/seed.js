/* Fictional public demo fixtures. No server records or credentials. */
globalThis.MorrowDemoSeed = function () {
  const at = (days, hour, minute = 0) => {
    const date = new Date(); date.setDate(date.getDate() + days); date.setHours(hour, minute, 0, 0);
    return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}:00`;
  };
  const message = (id, session_id, role, content, created_at, proactive = 0) => ({id,session_id,role,content,created_at,proactive,proactive_unread:0,attachments:[],artifacts:[],reasoning:'',versions:null,active:0});
  const nextFriday = ((5 - new Date().getDay() + 7) % 7) || 7;
  return {
    version: 1, nextId: 100,
    sessions: [
      {id:1,title:'薄荷又蔫了',archived:0,pinned:1,created_at:at(-1,15,30),updated_at:at(0,17,42)},
      {id:2,title:'雨天的番茄面',archived:0,pinned:0,created_at:at(0,17,30),updated_at:at(0,17,40)},
      {id:3,title:'下午，再问一句',archived:0,pinned:0,created_at:at(0,16,40),updated_at:at(0,16,50)}
    ],
    messages: [
      message(1,1,'user','薄荷又蔫了，是不是又浇多了',at(-1,15,30)),
      message(2,1,'assistant','上周你说它在窗台晒了一整天。这次先别浇，摸摸土。',at(-1,15,31)),
      message(3,1,'user','摸了，还是湿的',at(-1,15,34)),
      message(4,1,'assistant','那先别加水，挪到通风的地方，过两天再看。',at(-1,15,35)),
      message(5,2,'user','外面下雨了，冰箱只剩两个蛋和一个番茄。晚饭能凑一顿吗？',at(0,17,30)),
      message(6,2,'assistant','有挂面吗？番茄炒出汁，加水煮面，最后卧个蛋。另一个明早煎了夹吐司，刚好不用冒雨出门。',at(0,17,32)),
      message(7,2,'user','有！还摘了两片薄荷泡水，晚饭一下有了餐厅的排面。',at(0,17,34)),
      message(8,2,'assistant','配上窗外的雨声，今天这家店可以。先吃吧，挂面泡久了要变成面糊了。',at(0,17,36)),
      message(9,3,'assistant','薄荷挪到通风的地方了吗？',at(0,16,40),1),
      message(10,3,'user','挪了，叶子好像精神一点了',at(0,16,49)),
      message(11,3,'assistant','那就让它自己缓过来。今天先不折腾它了。',at(0,16,50))
    ],
    todos:[{id:1,text:'给薄荷换个通风的位置',done:0,created_at:at(0,9)},{id:2,text:'回家路上买番茄',done:1,created_at:at(0,10)}],
    notes:[{id:1,content:'面记得趁热吃。薄荷水的两片叶子，今天算餐厅特饮。',author:'assistant',created_at:at(0,17,38),updated_at:at(0,17,38)}],
    memories:[{id:1,content:'窗台养了一盆薄荷，最近容易浇水过多。',kind:'fixed',pinned:1,tags:['日常'],created_at:at(-7,15),updated_at:at(-1,15)},{id:2,content:'下雨天喜欢在家煮番茄鸡蛋面。',kind:'recent',pinned:0,tags:['做饭'],created_at:at(0,17),updated_at:at(0,17)}],
    diary:[{id:1,content:'下了一下午的雨。薄荷搬到了窗边，晚饭是番茄鸡蛋面。蛋卧成功了，汤有点多，盛了满满一大碗。',tag:'light',author:'user',favorite:0,comments:[],created_at:at(0,18),updated_at:at(0,18)}],
    alarms:[{id:1,session_id:1,note:'明天下午问问薄荷缓过来没有。',status:'reminded',source_text:'摸了，还是湿的',earliest_at:at(0,15),latest_at:at(0,17),created_at:at(0,2,22)},{id:2,session_id:1,note:'周五有考试，那天早点收住话头，让人早点睡。',status:'pending',source_text:'这几天要复习，不能又熬到一点。',earliest_at:at(nextFriday,20),latest_at:at(nextFriday,21),created_at:at(0,2,25)}],
    runs:[{id:1,session_id:1,title:'薄荷又蔫了',origin:'automatic',status:'completed',footprint:'翻了翻今天的聊天，把薄荷的事记下来了。',created_at:at(0,2,14),started_at:at(0,2,14),completed_at:at(0,2,31),activity_log:[
      {type:'tool',sequence:1,source:'builtin',name:'read_recent_chat',display_tool:'read_recent_chat',narrate:'翻了翻今天的聊天',status:'ok',duration_ms:182,args:{},at:at(0,2,15)},
      {type:'monologue',sequence:2,round:1,text:'薄荷又浇多了。明天下午问一句就好，不用一直提。',at:at(0,2,16)},
      {type:'tool',sequence:3,source:'builtin',name:'memory_add',display_tool:'memory_add',narrate:'把薄荷的事记进了记忆',status:'ok',duration_ms:96,args:{},at:at(0,2,17)},
      {type:'tool',sequence:4,source:'builtin',name:'set_alarm',display_tool:'set_alarm',narrate:'给明天夹了一张纸条',status:'ok',duration_ms:74,args:{},at:at(0,2,22)}
    ]}],
    favorites:[],keepsakes:[],
    settings:{identity:{brand_name:'Morrow',user_name:'你',assistant_name:'Morrow',assistant_pronoun:'他',user_name_set:false,assistant_name_set:false},persona:{content:'你是 Morrow。自然地聊日常，不展示内部实现细节。'},'together-date':{date:at(-127,12).slice(0,10)},'home-greetings':{mode:'timed',morning:'',noon:'',evening:'',common:''},'home-postcard':{enabled:false,content:'',image_url:'',caption:''},'weather/city':{city:''}}
  };
};
