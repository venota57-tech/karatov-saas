import React from "https://esm.sh/react@18"
import ReactDOM from "https://esm.sh/react-dom@18/client"

function App() {
  return (
    React.createElement("div", { style: { padding: "20px", fontFamily: "Arial" } },
      React.createElement("h1", null, "Отзывы"),
      React.createElement("button", {
        onClick: () => alert("Работает 🚀")
      }, "Обновить")
    )
  )
}

const root = ReactDOM.createRoot(document.getElementById("root"))
root.render(React.createElement(App))