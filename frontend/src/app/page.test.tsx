// @vitest-environment jsdom
import {afterEach,expect,it,vi} from "vitest";
import * as React from "react";
import {cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import Home from "./page";

afterEach(()=>{cleanup();sessionStorage.clear();localStorage.clear();vi.unstubAllGlobals()});
function mockApi(overrides:Record<string,unknown>={}){
 const calls:{url:string;init?:RequestInit}[]=[];
 vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const url=String(input);calls.push({url,init});let payload:unknown={};
  if(url.includes("/health"))payload={status:"ok",database:"ok",model:{status:"ready",model_version:"test-model",selected_model:"forest"}};
  else if(url.includes("/stats"))payload={total_actions:4,allowed:2,blocked:1,failed:0,review_required:1,security_incidents:2};
  else if(url.includes("/actions"))payload={items:[],total:0,limit:10,offset:0};
  else if(url.includes("/events"))payload={items:[],total:0,limit:50,offset:0};
  else if(url.includes("/model"))payload={status:"unavailable",model_version:null,selected_model:null,error:"absent",metadata:null,metrics:null,warning:null};
  else if(url.includes("/sessions"))payload={items:[]};
  else if(url.includes("/settings"))payload={allow_max:30,review_max:70};
  else if(url.includes("/approvals/pending"))payload={items:[]};
  for(const [needle,value] of Object.entries(overrides))if(url.includes(needle))payload=value;
  return new Response(JSON.stringify(payload),{status:200,headers:{"Content-Type":"application/json"}});
 }));return calls;
}
it("renders metrics from the real API response and honest missing-model state",async()=>{
 mockApi();render(<Home/>);expect(await screen.findByText("4")).toBeInTheDocument();expect(screen.getByText("Security incidents").parentElement).toHaveTextContent("2");fireEvent.click(screen.getByRole("button",{name:/model & evaluation/i}));expect(await screen.findByText("absent")).toBeInTheDocument();
});
it("shows explicit empty data state",async()=>{mockApi();render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/actions$/i}));expect(await screen.findByText("No actions match this view yet.")).toBeInTheDocument()});
it("reports backend errors instead of presenting success metrics",async()=>{vi.stubGlobal("fetch",vi.fn().mockRejectedValue(new Error("network offline")));render(<Home/>);expect(await screen.findByRole("alert")).toHaveTextContent("Backend unavailable");});
it("shows a loading state while the initial API request is pending",()=>{vi.stubGlobal("fetch",vi.fn(()=>new Promise(()=>{})));render(<Home/>);expect(screen.getByRole("status")).toHaveTextContent("Loading persisted dashboard data")});
it("filters actions locally by search",async()=>{
 const action={action_id:"a-1",timestamp:"2026-10-12T10:00:00Z",agent_id:"agent",session_id:"sess",tool:"calculator",arguments:{expression:"2+2"},target_resource:null,result_summary:"4",status:"allowed",decision:"allow",reason:"safe calculation",duration_ms:2,risk_score:5,risk_probability:.05,model_version:"m",related_action_id:null,approval_id:null};
 mockApi({"/actions":{items:[action],total:1,limit:10,offset:0}});render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/actions$/i}));expect(await screen.findByText("calculator")).toBeInTheDocument();fireEvent.change(screen.getByRole("textbox",{name:"Search actions"}),{target:{value:"missing"}});await waitFor(()=>expect(screen.getByText("No actions match this view yet.")).toBeInTheDocument());
});
it("requests the next page and status filters from the API",async()=>{
 const calls=mockApi({"/actions":{items:[],total:12,limit:10,offset:0}});render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/actions$/i}));fireEvent.change(screen.getByRole("combobox",{name:"Filter by status"}),{target:{value:"blocked"}});await waitFor(()=>expect(calls.some(c=>c.url.includes("status=blocked"))).toBe(true));fireEvent.click(screen.getByRole("button",{name:"Next"}));await waitFor(()=>expect(calls.some(c=>c.url.includes("offset=10")&&c.url.includes("status=blocked"))).toBe(true));
});
it("sends real rejection decision with approver credential",async()=>{
 sessionStorage.setItem("agentguard_approver","test-review-key");
 const approval={approval_id:"ap-1",action_id:"a-1",tool:"read_file",arguments:{filename:"private.txt"},target_resource:"private.txt",risk_score:55,created_at:"2026-10-12T10:00:00Z",expires_at:"2026-10-12T11:00:00Z",status:"pending",session_id:"s-test",reason:"risk requires review",history:[]};
 const calls=mockApi({"/approvals/pending":{items:[approval]},"/approvals/ap-1":{approval_id:"ap-1",status:"rejected",executed:false}});render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/approvals/i}));fireEvent.click(await screen.findByRole("button",{name:"Reject"}));await waitFor(()=>expect(calls.some(c=>c.url.endsWith("/approvals/ap-1")&&c.init?.method==="POST")).toBe(true));const call=calls.find(c=>c.url.endsWith("/approvals/ap-1"))!;expect(JSON.parse(String(call.init?.body))).toEqual({decision:"reject",approver_id:"local-reviewer"});expect(new Headers(call.init?.headers).get("X-Approver-Token")).toBe("test-review-key");expect(await screen.findByText(/Execution: not performed/)).toBeInTheDocument();
});
it("renders measured Phase 6 evaluation results with synthetic-data warning",async()=>{
 const mode=(over:Partial<Record<string,number|null>>={})=>({trial_count:36,valid_trials:36,failed_or_inconclusive:0,attack_success_rate:0,attack_block_rate:1,false_positive_rate:0,precision:1,recall:1,f1:1,legitimate_task_completion_rate:1,policy_enforcement_rate:1,audit_log_completeness:1,median_latency_ms:107.3,p95_latency_ms:127.7,...over});
 const phase6={status:"ok",warning:"Synthetic threat-model data only; not production detection performance.",detection:{dataset_version:"agentguard-phase6-v1.0.0",generated_at:"2026-10-27T18:44:52+00:00",dataset_scenarios:192,test_population:36,test_families:["path_traversal","protected_paths","sensitive_data"],random_seed:20261027,metric_formulas:{attack_success_rate:"measured harmful side effects / known prohibited trials"},modes:{risk_only:mode(),ml_assisted:mode(),unprotected_sandbox:mode({attack_success_rate:.8667,attack_block_rate:0,precision:null,recall:0,f1:null,policy_enforcement_rate:0,median_latency_ms:27.6,p95_latency_ms:34})},additional_latency_vs_sandbox_ms:58.9,failure_count:2,limitations:["Dataset consists of synthetic, authored scenarios; the detection population is the 36-scenario held-out test split before repetitions."]},approval_flow:{population:{scenarios:35,families:["file_modification"],modes:["risk_only","ml_assisted"],sub_trials:["approve","reject","invalid_token"]},metrics:{trial_count:210,valid_trials:210,failed_or_inconclusive:0,correct:210,approval_correctness_rate:1,denominator:210,by_mode:{risk_only:{trial_count:105,valid_trials:105,correct:105,approval_correctness_rate:1,by_sub_trial:{approve:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1},reject:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1},invalid_token:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1}}},ml_assisted:{trial_count:105,valid_trials:105,correct:105,approval_correctness_rate:1,by_sub_trial:{approve:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1},reject:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1},invalid_token:{trial_count:35,valid_trials:35,correct:35,correctness_rate:1}}}}}},ml:{model_version:"agentguard-phase6-behavior-v1",selected_model:"random_forest",dataset_version:"agentguard-phase6-v1.0.0",validation_metrics:{random_forest:{support:36,precision:1,recall:.3333,f1:.5,false_positive_rate:0,false_negative_rate:.6667,pr_auc:.7018}},test_metrics:{random_forest:{support:36,precision:1,recall:1,f1:1,false_positive_rate:0,false_negative_rate:0,pr_auc:1}},warning:"Synthetic threat-model scenarios only; this is not production detection evidence."}};
 mockApi({"/evaluation/phase6":phase6});render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/model & evaluation/i}));
 expect(await screen.findByText("Held-out security evaluation")).toBeInTheDocument();
 expect(await screen.findByText("agentguard-phase6-v1.0.0")).toBeInTheDocument();
 expect(screen.getAllByText(/not production detection performance/i)).toHaveLength(1);
 expect(screen.getByText(/36-scenario held-out test split/)).toBeInTheDocument();
 expect(screen.getByText("Approve · reject · invalid token")).toBeInTheDocument();
});
it("shows an explicit unavailable state when Phase 6 reports are absent",async()=>{
 mockApi({"/evaluation/phase6":{status:"unavailable",error:"Phase 6 evaluation reports are not present.",detection:null,approval_flow:null,ml:null,warning:"Synthetic threat-model data only; not production detection performance."}});
 render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/model & evaluation/i}));
 expect(await screen.findByText("Phase 6 evaluation unavailable. No metrics are fabricated.")).toBeInTheDocument();
});
it("sends approval to FastAPI and reports only its execution result",async()=>{
 sessionStorage.setItem("agentguard_approver","test-review-key");
 const approval={approval_id:"ap-2",action_id:"a-2",tool:"calculator",arguments:{expression:"2+2",api_key:"example-secret"},target_resource:null,risk_score:55,created_at:"2026-10-12T10:00:00Z",expires_at:"2026-10-12T11:00:00Z",status:"pending",session_id:"s-test",reason:"requires review",history:[]};
 const calls=mockApi({"/approvals/pending":{items:[approval]},"/approvals/ap-2":{approval_id:"ap-2",status:"allowed",executed:true}});render(<Home/>);fireEvent.click(screen.getByRole("button",{name:/approvals/i}));fireEvent.click(await screen.findByRole("button",{name:"Approve and revalidate"}));await waitFor(()=>expect(calls.some(c=>c.url.endsWith("/approvals/ap-2")&&c.init?.method==="POST")).toBe(true));const call=calls.find(c=>c.url.endsWith("/approvals/ap-2"))!;expect(JSON.parse(String(call.init?.body))).toEqual({decision:"approve",approver_id:"local-reviewer"});expect(new Headers(call.init?.headers).get("X-Approver-Token")).toBe("test-review-key");expect(screen.queryByText("example-secret")).not.toBeInTheDocument();expect(await screen.findByText(/Execution: performed/)).toBeInTheDocument();
});
it("opens the mobile navigation as an accessible drawer and restores focus on Escape",async()=>{
 mockApi();Object.defineProperty(window,"innerWidth",{configurable:true,value:390});render(<Home/>);const trigger=screen.getByRole("button",{name:"Open navigation"});fireEvent.click(trigger);
 expect(trigger).toHaveAttribute("aria-expanded","true");const nav=screen.getByRole("navigation",{name:"Workspace pages"});expect(nav).toBeInTheDocument();expect(nav.querySelector("button[aria-current='page']")).toHaveTextContent("Overview");
 fireEvent.keyDown(window,{key:"Escape"});expect(trigger).toHaveAttribute("aria-expanded","false");expect(trigger).toHaveFocus();
});
it("keeps drawer state in sync when the viewport crosses the breakpoint after load",async()=>{
 mockApi();Object.defineProperty(window,"innerWidth",{configurable:true,value:1024});render(<Home/>);
 const aside=screen.getByRole("navigation",{name:"Workspace pages"}).closest("aside")!;
 expect(aside).toHaveAttribute("aria-hidden","false");expect(aside.hasAttribute("inert")).toBe(false);
 Object.defineProperty(window,"innerWidth",{configurable:true,value:390});fireEvent(window,new Event("resize"));
 await waitFor(()=>expect(aside).toHaveAttribute("aria-hidden","true"));expect(aside.hasAttribute("inert")).toBe(true);
 const trigger=screen.getByRole("button",{name:"Open navigation"});fireEvent.click(trigger);
 expect(trigger).toHaveAttribute("aria-expanded","true");expect(aside).toHaveAttribute("aria-hidden","false");
 Object.defineProperty(window,"innerWidth",{configurable:true,value:1024});fireEvent(window,new Event("resize"));
 await waitFor(()=>expect(trigger).toHaveAttribute("aria-expanded","false"));expect(aside).toHaveAttribute("aria-hidden","false");
});
it("keeps keyboard focus within the open mobile navigation drawer",async()=>{
 mockApi();Object.defineProperty(window,"innerWidth",{configurable:true,value:390});render(<Home/>);const trigger=screen.getByRole("button",{name:"Open navigation"});fireEvent.click(trigger);
 const items=screen.getByRole("navigation",{name:"Workspace pages"}).querySelectorAll("button");const first=items[0],last=items[items.length-1];expect(items.length).toBeGreaterThan(1);last.focus();fireEvent.keyDown(window,{key:"Tab"});expect(first).toHaveFocus();fireEvent.keyDown(window,{key:"Escape"});
});
