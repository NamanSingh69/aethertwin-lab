import { it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Inspector } from "../src/components/Inspector";
import type { Metadata, Track } from "../src/lib/types";
const track: Track = {track_id:7,semantic_class:2,semantic_name:"Person",class_probs:[0,0,1,0,0,0,0],confidence:.8,position:[1,2,1],velocity:[0,0,0],obb_corners:[],predicted_trajectory:[],ttc_seconds:null,breached_geofences:[],state_kind:"predicted",last_observed_timestamp:10,measurement_age_seconds:1.25};
function inspect(value: Track) {
  const scene = {tracks:[value],geofences:[],diagnostics:{}} as unknown as Metadata;
  return renderToStaticMarkup(createElement(Inspector,{scene,selected:7,onSelect:()=>{},onFocus:()=>{},onClear:()=>{},latencies:[]}));
}
it("shows per-track prediction and source-time measurement age without replacing whole-scene freshness",()=>{
  const html=inspect(track);
  expect(html).toContain("Predicted · dashed box");
  expect(html).toContain("1.25 s");
  expect(html).toContain("warning still applies to the full scene");
});
it("does not invent observed state or age when tracking provenance is missing",()=>{
  const html=inspect({...track,state_kind:undefined,measurement_age_seconds:undefined});
  expect(html).not.toContain("Predicted · dashed box");
  expect(html).not.toContain("Measurement updated");
  expect(html).toContain("Not reported");
  expect(inspect({...track,state_kind:"measurement_updated",measurement_age_seconds:0})).toContain("Measurement updated");
});
