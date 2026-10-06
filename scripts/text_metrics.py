"""응답 텍스트 지표. 실호출 테스트와 response guard가 같은 정의를 쓴다."""


def korean_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("가" <= c <= "힣" for c in letters) / len(letters)
