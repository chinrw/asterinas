# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import re,sys
path=Path(sys.argv[1]);variant=int(sys.argv[2]);s=path.read_text()
def replace_function(text, signature, replacement):
 a=text.index(signature);start=text.index('{',a);level=1;end=start+1
 while level:
  if text[end]=='{':level+=1
  elif text[end]=='}':level-=1
  end+=1
 return text[:a]+replacement+text[end:]
if variant==0:
 insertion='''    fn insert(&self, timer_callback: Arc<TimerCallback>) {
        let diagnostic_start=crate::runtime_timeline::timer_operation_start();
        let mut callbacks=self.timer_callbacks.disable_irq().lock();
        callbacks.push(timer_callback);
        crate::runtime_timeline::timer_inserted(diagnostic_start, callbacks.len(), self as *const Self as u64);
    }'''
else:
 insertion='''    fn insert(&self, timer_callback: Arc<TimerCallback>) {
        let diagnostic_start=crate::runtime_timeline::timer_operation_start();
        let key=timer_callback.key();
        let mut callbacks=self.timer_callbacks.disable_irq().lock();
        let replaced=callbacks.insert(key,timer_callback);
        crate::runtime_timeline::timer_inserted(diagnostic_start, callbacks.len(), self as *const Self as u64);
        drop(callbacks);
        debug_assert!(replaced.is_none());
    }'''
s=replace_function(s,'    fn insert(',insertion)
s=s.replace('''    pub(crate) fn process_expired_timers(&self) {
        let callbacks = {''','''    pub(crate) fn process_expired_timers(&self) {
        let diagnostic_start=crate::runtime_timeline::timer_operation_start();
        let callbacks = {''',1)
s=s.replace('''            let mut callbacks = Vec::new();''','''            let before=timeout_list.len();
            let mut callbacks = Vec::new();''',1)
s=s.replace('''            callbacks
        };

        for callback in callbacks {''','''            crate::runtime_timeline::timer_collected(diagnostic_start,before-timeout_list.len(),callbacks.len(),self as *const Self as u64);
            callbacks
        };

        let diagnostic_start=crate::runtime_timeline::timer_operation_start();
        for callback in callbacks {''',1)
s=s.replace('''            callback.call();
        }
    }''','''            callback.call();
        }
        crate::runtime_timeline::timer_batch_done(diagnostic_start,self as *const Self as u64);
    }''',1)
s=s.replace('''        let Some(timer) = self.timer.upgrade() else {
            return;
        };''','''        let Some(timer) = self.timer.upgrade() else {
            crate::runtime_timeline::timer_callback(false);
            return;
        };
        crate::runtime_timeline::timer_callback(true);''',1)
s=s.replace('''        (timer.registered_callback)(timer_guard);''','''        let _timeline_scope=crate::runtime_timeline::timing_enabled().then(||crate::runtime_timeline::TimerScope::new(
            Arc::ptr_eq(&timer.timer_manager,crate::time::clocks::MonotonicClock::timer_manager()),self.expired_time));
        (timer.registered_callback)(timer_guard);''',1)
if variant==2:
 assert 'drop(retired);' in s
 s=s.replace('''        drop(retired);''','''        if retired.is_some(){crate::runtime_timeline::timer_retired();}
        drop(retired);''',1)
assert 'timer_collected(diagnostic_start' in s and 'timer_batch_done(diagnostic_start' in s
path.write_text(s)
p=path.parents[2]/'runtime_timeline.rs'
t=p.read_text();t,n=re.subn(r'const TIMER_QUEUE_VARIANT: u64 = \d+;',f'const TIMER_QUEUE_VARIANT: u64 = {variant};',t);assert n==1;p.write_text(t)
