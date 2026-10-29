import { StrictMode } from 'react';
import { NextIntlClientProvider } from 'next-intl';
import enMessages from '../../../../messages/en.json';
import actualWire from '@/__tests__/fixtures/studio-gpt-reference-wire.json';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { appAccessFetch } from '@/lib/appAccess';
import { activateRuntimeEnvironment, clearRuntimeEnvironment, requireActiveRuntimeEnvironment } from '@/lib/runtimeEnvironment';
import { beginStudioAssetImageRequest, listStudioAssetImageRequests } from '@/lib/studioAssetImageRequest';
import { usesStudioImageCore } from '@/lib/useStudioAssetImageRequests';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { useProjectStore, type Character } from '@/store/projectStore';
import { useToastStore } from '@/store/toastStore';
import { renderWithIntl } from '../../series/__tests__/test-utils';
import { ack, deferred, enriched, project } from './frame-command-fixtures';
import Cast from '../Cast';
import { activePolls } from '../cast/CastWorkbenchModal';

// The entire Cast / modal / model grid / strict request stack is real; only HTTP is replaced.
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('@/lib/appAccess', () => ({ appAccessFetch: vi.fn() }));
const SID = '11111111-1111-4111-8111-111111111111';
const models = ['gpt-image-2', 'gpt-image/gpt-image-2#image'];
const templateNegative = 'text, labels, watermark, UI overlay, panel borders, frames, multiple separate images';
const unsupported = 'GPT Image 2 不应用模板及风格负向提示词，也不支持随机种子、水印开关或参考图片输入。原提示词和风格设置会保留，切回支持的模型后继续使用。';
const owner = () => ({ environment: requireActiveRuntimeEnvironment(), rootKind: 'project' as const, rootId: 'a' });
function style(negative = '', positive = 'soft light') {
    return { selected_style_id: 'custom', custom_styles: [], ai_recommendations: [],
        style_config: { id: 'custom', is_custom: true, name: 'Saved style', positive_prompt: positive, negative_prompt: negative } };
}
type SnapshotOptions = { model?: string; source?: string; negative?: string; revision?: number; ratio?: string; seriesId?: string | null;
    art?: ReturnType<typeof style> | null; seriesRevision?: number; assetId?: string };
function snapshot({ model = 'gpt-image-2', source = 'episode', negative = '', revision = 5, ratio = '9:16', seriesId = null as string | null,
    art = negative ? style(negative) : null as ReturnType<typeof style> | null, seriesRevision = 1, assetId = 'shared' }: SnapshotOptions = {}) {
    return enriched({ ...ack([], 'a', revision), series_id: source === 'series' ? seriesId ?? 's' : seriesId,
        model_settings: { t2i_model: model, character_aspect_ratio: ratio }, art_direction: art,
        characters: [{ id: assetId, name: 'Alice', description: 'red coat', source: source as Character['source'] }] as (Character & { reference_sheet_request_id?: string })[] }, 9, seriesRevision);
}
let server: ReturnType<typeof snapshot>;
let series: { id: string; revision: number; art_direction: ReturnType<typeof style> | null };

function install(options: Parameters<typeof snapshot>[0] = {}) {
    server = snapshot({ revision: (useProjectStore.getState().currentProject?.revision ?? 3) + 1, ...options });
    useProjectStore.getState().applyProjectReadSnapshot('a', server);
    return server;
}
function wire(key: string, statuses = ['needs_confirmation', 'needs_confirmation']) {
    const root = useProjectStore.getState().currentProject!;
    const source = root.characters[0].source;
    const saved = actualWire.cases.find(item => item.model === models[0] && item.owner === (source === 'episode' ? 'local' : source))!;
    const result = structuredClone(saved.readback);
    return { ...result, request: { ...result.request, client_request_id: key, request_root_id: 'a',
        request_root_series_id: root.series_id ?? null, owner_id: source === 'global' ? 'library' : source === 'series' ? root.series_id! : 'a', asset_id: 'shared',
        members: statuses.map((status, i) => ({ variant_id: `${key}-${i}`, attempt_id: `${key}-${i}`, status,
            ...(status === 'completed' ? { output_ref: `assets/studio/${key}-${i}.png` } : {}) })),
    } };
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const posts = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'POST');
const gets = () => vi.mocked(appAccessFetch).mock.calls.filter(([, init]) => init?.method === 'GET');
const body = () => JSON.parse(String(posts()[0]?.[1]?.body));
const generate = () => screen.getByRole('button', { name: /生成第一组|再生成|发起新生成/ });
const select = (name: string) => fireEvent.click(screen.getByText(name, { selector: 'span' }).closest('button')!);
const open = () => {
    const button = screen.queryByRole('button', { name: '生成形象' });
    if (button) fireEvent.click(button); else fireEvent.click(screen.getByRole('img', { name: 'Alice' }));
};
function page(strict = false, english = false) {
    const content = <LightboxProvider><Cast /><ToastContainer /></LightboxProvider>;
    const localized = english ? <NextIntlClientProvider locale="en" messages={enMessages} timeZone="Asia/Shanghai">{content}</NextIntlClientProvider> : content;
    const view = renderWithIntl(strict ? <StrictMode>{localized}</StrictMode> : localized);
    if (english) fireEvent.click(screen.getByRole('button', { name: enMessages.cast.generateReference })); else open(); return view;
}
function seed(key = 'saved-gpt', model = models[0]) {
    return beginStudioAssetImageRequest({ ...owner(), input: { asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet',
        model_name: model, ...(model.includes('qwen') ? { negative_prompt: templateNegative } : {}), batch_size: 2 }, idFactory: () => key }).journal;
}
function lockQueue() {
    let release!: () => Promise<unknown>;
    const request = vi.fn((_name: string, _options: unknown, callback: (lock: object) => unknown) => new Promise((resolve, reject) => {
        release = async () => { try { resolve(await callback({})); } catch (error) { reject(error); } };
    }));
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } });
    return { request, release: () => release() };
}
function noSubmission() {
    expect(posts()).toHaveLength(0); expect(axios.post).not.toHaveBeenCalled();
    expect(listStudioAssetImageRequests(owner())).toHaveLength(0);
}
beforeEach(() => {
    vi.resetAllMocks(); localStorage.clear(); useToastStore.getState().clear();
    activateRuntimeEnvironment({ schema_version: 1, storage_id: SID });
    useProjectStore.setState({ currentProject: project(), projects: [project()], currentSeries: null, readWatermarks: {}, generatingTasks: [], runningOps: {} });
    series = { id: 's', revision: 1, art_direction: null };
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_name: string, _options: unknown, callback: (lock: object) => unknown) => callback({}) } });
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/presets') ? [] : String(url).includes('/series/') ? series : server }));
    vi.mocked(axios.post).mockResolvedValue({ data: { ...ack([], 'a', 6), _task_id: 'legacy' } });
    vi.mocked(appAccessFetch).mockImplementation(async (url, init) => {
        const input = init?.method === 'POST' ? JSON.parse(String(init.body)) : null;
        const key = input?.client_request_id ?? String(url).split('/').at(-1)!;
        return response(wire(key, Array.from({ length: input?.batch_size ?? 2 }, () => 'needs_confirmation')));
    });
    install();
});
afterEach(() => { activePolls.forEach(clearInterval); activePolls.clear(); vi.useRealTimers(); clearRuntimeEnvironment(); });

describe('real Cast GPT reference-sheet requests', () => {
    it.each(models)('submits %s with the existing receipt instead of the legacy path', async model => {
        install({ model }); page(); fireEvent.change(screen.getByRole('textbox'), { target: { value: 'my GPT sheet' } });
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({ asset_id: 'shared', asset_type: 'character', generation_type: 'reference_sheet', model_name: model,
            prompt: 'my GPT sheet', apply_style: true, batch_size: 2 });
        for (const field of ['negative_prompt','seed','watermark','reference_image_url','quality','image_contract_version']) expect(body()).not.toHaveProperty(field);
        expect(String(posts()[0][0])).toMatch(/\/projects\/a\/asset_image_requests$/); expect(axios.post).not.toHaveBeenCalled();
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
    });
    it.each(['episode','series','global'])('preserves %s ownership at the existing Cast entry', async source => {
        install({ source }); page(); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body().batch_size).toBe(2); expect(listStudioAssetImageRequests(owner())).toHaveLength(1);
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2); expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['9:16','3:4','1:1','4:3','16:9'])('preserves %s ratio and omitted quality', async ratio => {
        page(); fireEvent.click(screen.getByRole('button',{name:ratio})); fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        if(ratio==='9:16') expect(body()).not.toHaveProperty('aspect_ratio'); else expect(body().aspect_ratio).toBe(ratio);
        expect(body()).not.toHaveProperty('quality');
    });
    it.each(models)('shows unsupported controls explicitly for %s without mutating the style', async model => {
        const art=style('keep this negative '+ 'x'.repeat(501)); install({ model, art }); const before=structuredClone(useProjectStore.getState().currentProject!.art_direction); page();
        expect(screen.getByText(unsupported)).toBeInTheDocument(); expect(screen.getByText('负向提示词（本次不应用）')).toBeInTheDocument();
        expect(screen.getByText(art.style_config.negative_prompt)).toBeInTheDocument(); expect(generate()).toBeEnabled();
        fireEvent.click(generate()); await waitFor(() => expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({style_prompt:'soft light',apply_style:true}); expect(body()).not.toHaveProperty('negative_prompt');
        expect(useProjectStore.getState().currentProject!.art_direction).toEqual(before);
    });
    it('preserves Qwen prompt, template, ratio and style when switching to GPT and back', async () => {
        install({model:'qwen-image-2.0',art:style('retain negative')}); page();
        fireEvent.change(screen.getByRole('textbox'),{target:{value:'my unsaved Qwen prompt'}}); fireEvent.click(screen.getByRole('button',{name:'1:1'}));
        select('GPT Image 2'); expect(screen.getByText(unsupported)).toBeInTheDocument(); expect(screen.getByRole('textbox')).toHaveValue('my unsaved Qwen prompt');
        select('Qwen Image 2.0'); expect(screen.queryByText(unsupported)).toBeNull(); expect(screen.getByRole('textbox')).toHaveValue('my unsaved Qwen prompt');
        fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({model_name:'qwen-image-2.0',prompt:'my unsaved Qwen prompt',aspect_ratio:'1:1',negative_prompt:templateNegative,apply_style:true});
        expect(useProjectStore.getState().currentProject!.art_direction!.style_config.negative_prompt).toBe('retain negative');
    });
    it.each(models)('uses fresh positive style only once for %s and explains unused inherited negative', async model => {
        install({model,seriesId:'s',source:'global'});series.art_direction=style('inherited negative','inherited positive');page();
        fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));
        expect(vi.mocked(axios.get).mock.calls.some(([url])=>String(url).endsWith('/series/s'))).toBe(true);
        expect(body()).toMatchObject({style_prompt:'inherited positive',apply_style:true});expect(body()).not.toHaveProperty('negative_prompt');expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(models)('routes %s only for supported asset T2I tuples', model => {
        expect(usesStudioImageCore('character',model,'reference_sheet')).toBe(true);
        for(const [kind,channel] of [['character','full_body'],['scene','image'],['prop','image']]) expect(usesStudioImageCore(kind,model,channel)).toBe(true);
        for(const [kind,channel] of [['character','image'],['scene','reference_sheet'],['prop','full_body'],['storyboard_frame','image']]) expect(usesStudioImageCore(kind,model,channel)).toBe(false);
    });
    it.each(['lost ACK','HTTP 503'])('keeps %s on the original GPT key and only reads it after reopening',async failure=>{
        vi.mocked(appAccessFetch).mockImplementationOnce(async()=>{if(failure==='lost ACK')throw new Error('lost ACK');return response({detail:'unavailable'},503);});
        page();fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        const key=body().client_request_id;fireEvent.click(screen.getByRole('button',{name:'关闭'}));open();await waitFor(()=>expect(gets()).toHaveLength(1));
        expect(String(gets()[0][0])).toContain(key);expect(posts()).toHaveLength(1);expect(axios.post).not.toHaveBeenCalled();expect(listStudioAssetImageRequests(owner())[0].request.model_name).toBe(models[0]);
    });
    it('guards double clicks across a held project preflight',async()=>{
        page();const read=deferred<{data:typeof server}>();vi.mocked(axios.get).mockReturnValueOnce(read.promise);fireEvent.click(generate());fireEvent.click(generate());
        await act(async()=>read.resolve({data:server}));await waitFor(()=>expect(posts()).toHaveLength(1));expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['model','ratio','owner','series'])('refuses changed %s from the fresh project read',async change=>{
        page();server=snapshot({revision:6,...(change==='model'?{model:'qwen-image-2.0'}:change==='ratio'?{ratio:'1:1'}:change==='owner'?{source:'global'}:{seriesId:'new'})});
        fireEvent.click(generate());await act(async()=>{});noSubmission();
    });
    it.each(['environment','close','model','draft'])('invalidates %s A→B→A while reading the project',async change=>{
        page();const read=deferred<{data:typeof server}>();vi.mocked(axios.get).mockReturnValueOnce(read.promise);const original=(screen.getByRole('textbox') as HTMLTextAreaElement).value;fireEvent.click(generate());
        act(()=>{if(change==='environment'){activateRuntimeEnvironment({schema_version:1,storage_id:'44444444-4444-4444-8444-444444444444'});activateRuntimeEnvironment({schema_version:1,storage_id:SID});}
        else if(change==='close'){fireEvent.click(screen.getByRole('button',{name:'关闭'}));open();}
        else if(change==='model'){select('Qwen Image 2.0');select('GPT Image 2');}
        else{fireEvent.change(screen.getByRole('textbox'),{target:{value:'changed'}});fireEvent.change(screen.getByRole('textbox'),{target:{value:original}});}});
        await act(async()=>read.resolve({data:server}));noSubmission();
    });
    it.each(['style','owner','model','series','close','environment','batch ABA'])('revalidates %s after waiting for the cross-tab lock',async change=>{
        install({art:style('saved')});page();const lock=lockQueue();fireEvent.click(generate());await waitFor(()=>expect(lock.request).toHaveBeenCalledOnce());
        act(()=>{if(change==='close')fireEvent.click(screen.getByRole('button',{name:'关闭'}));
        else if(change==='environment'){activateRuntimeEnvironment({schema_version:1,storage_id:'44444444-4444-4444-8444-444444444444'});activateRuntimeEnvironment({schema_version:1,storage_id:SID});}
        else if(change==='batch ABA'){expect(screen.getByRole('button',{name:'×4'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'×4'}));fireEvent.click(screen.getByRole('button',{name:'×2'}));}
        else install({revision:8,art:style(change==='style'?'new style':'saved'),...(change==='owner'?{source:'global'}:change==='model'?{model:'qwen-image-2.0'}:change==='series'?{seriesId:'other'}:{})});});
        await act(async()=>{await lock.release();});if(change==='batch ABA'){expect(posts()).toHaveLength(1);expect(body().batch_size).toBe(2);expect(axios.post).not.toHaveBeenCalled();}else noSubmission();
    });
    it('does not borrow a shared source A→B→A while the project revision stays constant',async()=>{
        install({source:'global',art:style('same')});page();const lock=lockQueue();fireEvent.click(generate());await waitFor(()=>expect(lock.request).toHaveBeenCalledOnce());
        const initial=server;
        act(()=>{useProjectStore.getState().applyProjectReadSnapshot('a',enriched({...initial,characters:[{...initial.characters[0],description:'changed'}]},10,1));
            useProjectStore.getState().applyProjectReadSnapshot('a',enriched(initial,11,1));});
        expect(useProjectStore.getState().currentProject?.revision).toBe(initial.revision);expect(useProjectStore.getState().currentProject?.read_versions?.library).toBe(11);
        await act(async()=>{await lock.release();});noSubmission();
    });
    it('restores an old GPT journal under Qwen and an old Qwen journal under GPT without POST',async()=>{
        seed('old-gpt');install({model:'qwen-image-2.0'});page(true);await waitFor(()=>expect(gets().length).toBeGreaterThan(0));
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);expect(posts()).toHaveLength(0);expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button',{name:'关闭'}));localStorage.clear();seed('old-qwen','qwen-image-2.0');install({model:models[0]});open();
        await waitFor(()=>expect(gets().some(([url])=>String(url).endsWith('/old-qwen'))).toBe(true));expect(posts()).toHaveLength(0);expect(axios.post).not.toHaveBeenCalled();
    });
    it('does not let a late receipt enter another owner session',async()=>{
        seed();const late=deferred<Response>();vi.mocked(appAccessFetch).mockReturnValueOnce(late.promise);page();await waitFor(()=>expect(gets()).toHaveLength(1));
        const old=wire('saved-gpt',['completed','completed']);fireEvent.click(screen.getByRole('button',{name:'关闭'}));act(()=>install({assetId:'other'}));open();
        await act(async()=>late.resolve(response(old)));expect(screen.queryByTestId('studio-image-member')).toBeNull();expect(posts()).toHaveLength(0);expect(axios.post).not.toHaveBeenCalled();
    });
    it('supports style-off without deleting the saved negative and positive values',async()=>{
        install({art:style('retain negative')});page();fireEvent.click(screen.getByRole('button',{name:'风格基线'}));fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));
        expect(body()).toMatchObject({apply_style:false,style_prompt:''});expect(body()).not.toHaveProperty('negative_prompt');expect(server.art_direction?.style_config.negative_prompt).toBe('retain negative');
    });
    it.each(models)('does not treat existing uploaded character artwork as an input reference for %s',async model=>{
        install({model});server={...server,characters:[{...server.characters[0],base_character_id:'parent',three_view_asset:{selected_id:'upload',variants:[{id:'upload',url:'uploads/old.png',created_at:1,is_uploaded_source:true}]}}]};
        useProjectStore.getState().applyProjectReadSnapshot('a',server);page();fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));expect(body()).not.toHaveProperty('reference_image_url');expect(axios.post).not.toHaveBeenCalled();
    });
    it.each(['gpt-image-2','gpt-image/gpt-image-2#image'])('unlocks the existing design-sheet template for %s',async model=>{
        install({model});page();fireEvent.click(screen.getByRole('button',{name:/角色设定图/}));fireEvent.click(generate());await waitFor(()=>expect(posts()).toHaveLength(1));
        expect(body().prompt).toContain('professional character design sheet');expect(body().model_name).toBe(model);
    });

    it('shows the unsupported-input explanation using the real English locale',()=>{
        install({art:style('retained negative')});page(false,true);
        expect(screen.getByText(enMessages.castWorkbench.gptReferenceUnsupported)).toBeInTheDocument();
        expect(screen.getByText(enMessages.castWorkbench.gptNegativeNotApplied)).toBeInTheDocument();
    });
    it.each([false,true])('only retires completed GPT history after a readable project; failure=%s',async failRead=>{
        seed();const completed=wire('saved-gpt',['completed','completed']);
        vi.mocked(appAccessFetch).mockResolvedValue(response(completed));
        const confirmed={...server,revision:server.revision+1,read_versions:{...server.read_versions,project:server.revision+1},characters:[{...server.characters[0],
            image_url:completed.request.members[0].output_ref!,reference_sheet_request_id:completed.request.id,reference_sheet:{selected_image_id:'saved-gpt-0',image_variants:completed.request.members.map(member=>({id:member.variant_id,url:member.output_ref!,created_at:1}))}}]};
        vi.mocked(axios.get).mockImplementation(async url=>{if(String(url).endsWith('/presets'))return{data:[]};if(failRead)throw new Error('offline');return{data:confirmed};});
        page();expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        await waitFor(()=>expect(listStudioAssetImageRequests(owner())).toHaveLength(failRead?1:0));
        if(failRead)expect(await screen.findByText('结果已保存，但最新素材读取失败，请再次查看。')).toBeInTheDocument();
        else expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('saved-gpt-0');
        expect(posts()).toHaveLength(0);expect(axios.post).not.toHaveBeenCalled();
    });
    it('retains partial GPT completion and the original user selection without resubmitting',async()=>{
        server={...server,characters:[{...server.characters[0],reference_sheet:{selected_image_id:'original',image_variants:[{id:'original',url:'/original.png',created_at:1}]}}]};useProjectStore.getState().applyProjectReadSnapshot('a',server);
        seed();vi.mocked(appAccessFetch).mockResolvedValue(response(wire('saved-gpt',['completed','needs_confirmation'])));page();
        expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);await act(async()=>{});
        expect(listStudioAssetImageRequests(owner())).toHaveLength(1);expect(useProjectStore.getState().currentProject?.characters[0].reference_sheet?.selected_image_id).toBe('original');
        expect(posts()).toHaveLength(0);expect(axios.post).not.toHaveBeenCalled();
    });
    it('keeps a mismatched receipt unknown without falling back to a second request',async()=>{
        vi.mocked(appAccessFetch).mockImplementation(async()=>response(wire('a-different-key')));page();fireEvent.click(generate());
        await waitFor(()=>expect(posts()).toHaveLength(1));expect(await screen.findAllByTestId('studio-image-member')).toHaveLength(2);
        const request=listStudioAssetImageRequests(owner())[0];expect(request.receiptIdentity).toBeNull();
        fireEvent.click(screen.getByRole('button',{name:'查看生成结果'}));await waitFor(()=>expect(gets()).toHaveLength(1));
        expect(String(gets()[0][0])).toContain(request.request.client_request_id);expect(posts()).toHaveLength(1);expect(axios.post).not.toHaveBeenCalled();
    });

});
