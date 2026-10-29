// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import actualWire from './fixtures/studio-gpt-reference-wire.json';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests, readStudioAssetImageRequestReceipt,
    submitStudioAssetImageRequest, type StudioAssetImageRequestInput } from '@/lib/studioAssetImageRequest';
import { validateStudioAssetImageReceipt } from '@/lib/studioAssetImageContract';
import { usesStudioImageCore } from '@/lib/useStudioAssetImageRequests';
vi.mock('@/lib/appAccess',()=>({appAccessFetch:vi.fn()}));
const models=['gpt-image-2','gpt-image/gpt-image-2#image'];
const owner=()=>({environment:requireActiveRuntimeEnvironment(),rootKind:'project' as const,rootId:'p'});
const input=(model=models[0])=>({asset_id:'c',asset_type:'character' as const,generation_type:'reference_sheet' as const,model_name:model,prompt:'Author prompt',apply_style:false,batch_size:2});
beforeEach(()=>{vi.resetAllMocks();localStorage.clear();activateRuntimeEnvironment({schema_version:1,storage_id:'11111111-1111-4111-8111-111111111111'});
    Object.defineProperty(navigator,'locks',{configurable:true,value:{request:async(_name:string,_options:unknown,callback:(lock:object)=>unknown)=>callback({})}});
});
afterEach(()=>{clearRuntimeEnvironment();});
describe('existing Studio GPT reference-sheet request contract',()=>{
    it.each(actualWire.cases)('parses actual $model $owner safe pending and completed receipts',item=>{
        expect(validateStudioAssetImageReceipt(item.admission)).toEqual(item.admission);
        expect(validateStudioAssetImageReceipt(item.readback)).toEqual(item.readback);
        expect(item.readback.request.members.every(member=>member.status==='completed')).toBe(true);
    });
    it.each(models)('admits exact %s for existing asset T2I tuples only',model=>{
        expect(usesStudioImageCore('character',model,'reference_sheet')).toBe(true);
        for(const [kind,channel] of [['character','full_body'],['scene','image'],['prop','image']])expect(usesStudioImageCore(kind,model,channel)).toBe(true);
        for(const [kind,channel] of [['character','image'],['scene','reference_sheet'],['prop','full_body'],['storyboard_frame','image']])expect(usesStudioImageCore(kind,model,channel)).toBe(false);
    });
    it.each(['openai/gpt-image-2#image','gpt-image-2-extra','gpt-image-2.0','GPT-image-2'])('does not guess unsupported alias %s',model=>{
        expect(usesStudioImageCore('character',model,'reference_sheet')).toBe(false);
    });
    it.each(models.flatMap(model=>[
        ['negative',model,{negative_prompt:'discarded input'}],['whitespace negative',model,{negative_prompt:' '}],['seed zero',model,{seed:0}],
        ['watermark false',model,{watermark:false}],['watermark true',model,{watermark:true}],['empty reference',model,{reference_image_url:''}],
        ['reference',model,{reference_image_url:'uploads/existing.png'}],['character image channel',model,{generation_type:'image'}],['scene reference-sheet channel',model,{asset_type:'scene',generation_type:'reference_sheet'}],
    ] as const))('rejects new unsupported %s on %s before journal or network',async(_field,model,extra)=>{
        await expect(submitStudioAssetImageRequest({...owner(),input:{...input(model),...extra} as StudioAssetImageRequestInput,idFactory:()=> 'new-gpt'})).rejects.toThrow(/studio_image/);
        expect(localStorage.length).toBe(0);expect(appAccessFetch).not.toHaveBeenCalled();
    });
    it.each([{}, {negative_prompt:null}, {negative_prompt:''}, {reference_image_url:null}])('preserves supported omission/null/empty presence %j',extra=>{
        const request={...input(),...extra};const result=beginStudioAssetImageRequest({...owner(),input:request,idFactory:()=> 'presence'});
        expect(result.journal.request).toEqual({...request,client_request_id:'presence'});
    });
    it('keeps original historical GPT fields for GET and does not pay or rewrite the request',async()=>{
        const saved=beginStudioAssetImageRequest({...owner(),input:input(),idFactory:()=> 'gpt-sheet-request'});
        const key=localStorage.key(0)!;const old={...saved.journal,request:{...saved.journal.request,seed:0,watermark:false,negative_prompt:'historical raw'}};
        localStorage.setItem(key,JSON.stringify(old));
        const existing=listStudioAssetImageRequests(owner())[0];expect(existing.request).toEqual(old.request);
        const sample=actualWire.cases.find(item=>item.owner==='local'&&item.model===models[0])!;
        vi.mocked(appAccessFetch).mockResolvedValue(new Response(JSON.stringify(sample.readback),{status:200}));
        expect((await readStudioAssetImageRequestReceipt({...owner(),journal:existing})).status).toBe('confirmed');
        expect(JSON.parse(localStorage.getItem(key)!).request).toEqual(old.request);
        expect(vi.mocked(appAccessFetch).mock.calls[0][1]?.method).toBe('GET');
        expect((await submitStudioAssetImageRequest({...owner(),input:{...input(),seed:0,watermark:false,negative_prompt:'historical raw'},idFactory:()=> 'gpt-sheet-request'})).reason).toBe('studio_image_request_already_recorded');
        expect(appAccessFetch).toHaveBeenCalledTimes(1);
    });
});

describe('GPT reference-free remaining asset request admission', () => {
    const targets = [
        { asset_type: 'character', generation_type: 'full_body' },
        { asset_type: 'scene', generation_type: 'all' },
        { asset_type: 'prop', generation_type: 'all' },
    ] as const;
    it.each(models.flatMap(model => targets.map(target => ({ model, ...target }))))(
        'retains exact raw $asset_type/$generation_type with $model in the existing journal', ({ model, ...target }) => {
            const raw = { ...input(model), ...target, negative_prompt: '', apply_style: true };
            const created = beginStudioAssetImageRequest({ ...owner(), input: raw, idFactory: () => 'remaining-t2i' });
            expect(created.status).toBe('created');
            expect(created.journal.schema_version).toBe(1);
            expect(created.journal.request).toEqual({ ...raw, client_request_id: 'remaining-t2i' });
            expect(listStudioAssetImageRequests(owner())).toEqual([created.journal]);
            expect(appAccessFetch).not.toHaveBeenCalled();
        },
    );
    it.each([
        { label: 'full-body seed zero', target: targets[0], extra: { seed: 0 } },
        { label: 'scene watermark false', target: targets[1], extra: { watermark: false } },
        { label: 'prop whitespace negative', target: targets[2], extra: { negative_prompt: ' ' } },
        { label: 'scene reference input', target: targets[1], extra: { reference_image_url: 'uploads/existing.png' } },
        { label: 'prop null seed', target: targets[2], extra: { seed: null } },
        { label: 'full-body null watermark', target: targets[0], extra: { watermark: null } },
    ])('rejects $label before storing or sending a new request', async ({ target, extra }) => {
        const raw = { ...input(), ...target, ...extra } as StudioAssetImageRequestInput;
        await expect(submitStudioAssetImageRequest({ ...owner(), input: raw, idFactory: () => 'invalid-remaining' })).rejects.toThrow(/studio_image/);
        expect(listStudioAssetImageRequests(owner())).toEqual([]);
        expect(appAccessFetch).not.toHaveBeenCalled();
    });
});
