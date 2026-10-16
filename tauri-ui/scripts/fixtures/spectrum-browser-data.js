const count=16, currents=Number(new URLSearchParams(location.search).get("currents"))||12, pixels=8192;
const records=Array.from({length:count},(_,index)=>({id:`perf-${index}`,storage:'database',sourcePath:`/synthetic/${index}.xlsx`,fileName:`${index}.xlsx`,version:'perf-v1',modifiedAtMs:1792026000000-index*60000,timeMs:1792026000000-index*60000,timeSource:'session',sn:`PERF-${String(index).padStart(2,'0')}`,snSource:'metadata',station:'性能夹具',stationId:'synthetic',temperature:'25°C',testMode:'automatic',sessionId:`perf-session-${index}`,flowStatus:'completed',readStatus:'readable',error:'',pointCount:currents,validPointCount:currents,minimumCurrentA:1,maximumCurrentA:currents}));
window.__spectrumRequests=[];
export async function demoDataRequest(method,params){
 if(method==='dataBrowser.list'){
  const filtered=records.filter(r=>!params.sn||r.sn===params.sn);
  return {records:filtered,total:filtered.length,fileCount:filtered.length,page:1,pageCount:1,rootPath:'合成性能夹具',indexedAtMs:1792026000000,warnings:[],errorCount:0,stations:['性能夹具'],statuses:['completed']};
 }
 if(method==='dataBrowser.inspect'){
  const record=records.find(r=>r.id===params.recordId);if(!record)throw Error('unknown synthetic record');
  const index=records.indexOf(record);
  const points=Array.from({length:currents},(_,i)=>({rowId:i+2,currentA:i+1,voltageV:2+(i+1)*.02,powerW:(i+1)*1.5,efficiencyPercent:40,peakWavelengthNm:976.2,centroidNm:976.15,fwhmNm:.5,pibPercent:96,smsrDb:35}));
  const detail={record:{...record},points,metadata:{'来源':'合成夹具'},warnings:[],columnLabels:{}};
  if(params.includeSpectra){
   window.__spectrumRequests.push(record.id);
   detail.spectra=points.map((p,i)=>{
    const wavelengthNm=Array.from({length:pixels},(_,j)=>970+j*12/(pixels-1));
    const intensity=wavelengthNm.map(w=>50+(10000+i*200+index*100)*Math.exp(-(((w-976.2-i*.005)/.4)**2)));
    let peak=0;for(let j=1;j<pixels;j++)if(intensity[j]>intensity[peak])peak=j;
    return {currentA:p.currentA,wavelengthNm,intensity,peakWavelengthNm:wavelengthNm[peak],maxIntensity:intensity[peak]};
   });
  }
  return detail;
 }
 throw Error(`Unexpected fixture method ${method}`);
}
