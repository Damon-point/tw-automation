"""로컬 탑재 에디터 헬퍼 — 실제 에디터를 앱 안에서 그대로 띄운다.

- TinyMCE (Confluence 계열): /static/tinymce 자가호스팅
- Naver SmartEditor2 (Service Portal): /static/se2 자가호스팅 (GNU LGPL, 프리빌드 배포본)

모두 로컬 자산으로만 동작(외부 CDN·프로덕션 무관). 편집 결과는 Python 콜백으로 회수해 로컬 저장.
"""
import json

from nicegui import ui


# ── 라이브러리 로딩 (반드시 페이지 생성 시점에 호출) ──────────────────
# add_head_html 은 페이지가 처음 렌더될 때만 <head> 에 주입된다. 이벤트 핸들러 안에서
# 호출하면 반영되지 않으므로, 각 페이지의 @ui.page 함수 본문에서 아래를 호출해 둔다.
def preload_tinymce() -> None:
    ui.add_head_html(
        '<script src="/static/tinymce/tinymce.min.js" referrerpolicy="origin"></script>'
    )


def preload_smarteditor2() -> None:
    ui.add_head_html('<script src="/static/se2/js/service/HuskyEZCreator.js"></script>')


# ── TinyMCE (Confluence 편집) ─────────────────────────────────────────
def tinymce_editor(el_id: str, initial_html: str) -> None:
    """TinyMCE 를 el_id textarea 에 초기화하고 initial_html 을 불러온다.

    (라이브러리는 preload_tinymce() 로 페이지 생성 시점에 미리 로드되어 있어야 함.)
    """
    ui.html(
        f'<textarea id="{el_id}" style="width:100%;min-height:300px;"></textarea>',
        sanitize=False,
    ).classes("w-full")
    ui.run_javascript(
        f"""
        (function() {{
          var content = {json.dumps(initial_html)};
          function init() {{
            if (!window.tinymce) {{ setTimeout(init, 100); return; }}
            if (tinymce.get({json.dumps(el_id)})) return;
            tinymce.init({{
              selector: "#" + {json.dumps(el_id)},
              base_url: "/static/tinymce",
              license_key: "gpl",
              promotion: false,
              height: 640,
              menubar: true,
              branding: false,
              plugins: "table lists link image code fullscreen searchreplace visualblocks",
              toolbar: "undo redo | blocks | bold italic underline forecolor backcolor | "
                     + "alignleft aligncenter alignright | bullist numlist | table link image | "
                     + "removeformat | code fullscreen",
              init_instance_callback: function(ed) {{ ed.setContent(content); }}
            }});
          }}
          init();
        }})();
        """
    )


async def tinymce_get_content(el_id: str) -> str:
    """현재 TinyMCE 편집 내용(HTML)을 가져온다."""
    return await ui.run_javascript(
        f'tinymce.get({json.dumps(el_id)}) ? tinymce.get({json.dumps(el_id)}).getContent() : ""',
        timeout=15,
    )


# ── Naver SmartEditor2 (Service Portal 편집) ──────────────────────────
def smarteditor2_editor(el_id: str, initial_html: str) -> None:
    """SmartEditor2 를 el_id textarea 에 초기화하고 initial_html 을 불러온다.

    (라이브러리는 preload_smarteditor2() 로 페이지 생성 시점에 미리 로드되어 있어야 함.)
    """
    ui.html(
        f'<textarea id="{el_id}" style="width:100%;height:400px;"></textarea>',
        sanitize=False,
    ).classes("w-full")
    ui.run_javascript(
        f"""
        (function() {{
          var content = {json.dumps(initial_html)};
          function init() {{
            if (!(window.nhn && window.nhn.husky && window.nhn.husky.EZCreator)) {{
              setTimeout(init, 100); return;
            }}
            var ph = document.getElementById({json.dumps(el_id)});
            if (!ph || ph.dataset.se2inited) return;
            ph.dataset.se2inited = "1";
            ph.value = content;  // SE2 는 placeholder 값을 초기 본문으로 읽는다
            window.oEditors = window.oEditors || [];
            window.nhn.husky.EZCreator.createInIFrame({{
              oAppRef: window.oEditors,
              elPlaceHolder: {json.dumps(el_id)},
              sSkinURI: "/static/se2/SmartEditor2Skin.html",
              fCreator: "createSEditor2",
              htParams: {{ bUseToolbar: true, bUseVerticalResizer: true, bUseModeChanger: true }}
            }});
          }}
          init();
        }})();
        """
    )


async def smarteditor2_get_content(el_id: str) -> str:
    """현재 SmartEditor2 편집 내용(HTML)을 가져온다."""
    return await ui.run_javascript(
        f"""
        (function() {{
          try {{ window.oEditors.getById[{json.dumps(el_id)}].exec("UPDATE_CONTENTS_FIELD", []); }}
          catch (e) {{}}
          var el = document.getElementById({json.dumps(el_id)});
          return el ? el.value : "";
        }})()
        """,
        timeout=15,
    )
