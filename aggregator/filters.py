def register_filters(app):

    @app.template_filter("get_whats_happening")
    def get_whats_happening(summary):
        if not summary:
            return None
        try:
            start_marker = "What's happening:"
            end_marker = "What's next:"
            if start_marker in summary:
                parts = summary.split(start_marker)
                content = parts[1]
                if end_marker in content:
                    content = content.split(end_marker)[0]
                return content.strip()
        except Exception:
            pass
        return None

    @app.template_filter("get_the_story")
    def get_the_story(report):
        if not report:
            return None
        try:
            markers = [
                "The story:",
                "What happened:",
                "The discovery or development:",
                "The discovery:",
                "The development:"
            ]
            start_marker = None
            for m in markers:
                if m in report:
                    start_marker = m
                    break
            if not start_marker:
                return None
            next_sections = [
                "How the left is covering it:", "Why it matters:",
                "What's the game or company:", "Key performances:",
                "Market impact:", "What experts are saying:",
                "Key details:", "Different perspectives:",
                "What the research shows:", "What the coverage is saying:",
                "The bigger picture:", "Market impact:"
            ]
            content = report.split(start_marker)[1]
            end_pos = len(content)
            for next_m in next_sections:
                pos = content.find(next_m)
                if pos != -1 and pos < end_pos:
                    end_pos = pos
            return content[:end_pos].strip()
        except Exception:
            pass
        return None

    @app.template_filter("get_big_picture")
    def get_big_picture(summary):
        if not summary:
            return None
        try:
            start_marker = "The big picture:"
            end_marker = "Why it matters:"
            if start_marker in summary:
                parts = summary.split(start_marker)
                content = parts[1]
                if end_marker in content:
                    content = content.split(end_marker)[0]
                return content.strip()
        except Exception:
            pass
        return None
