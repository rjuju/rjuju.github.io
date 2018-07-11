---
layout: page
title: PostgreSQL
excerpt: "An archive of PostgreSQL related blog posts sorted by date."
search_omit: true
---

<ul class="post-list">
{% for post in site.categories.postgresql %}
 <li>
  <article>
   <a href="{{ site.url }}{{ post.url }}">
    <div class="flag-icon flag-icon-{{ post.lang }}"></div>{{ post.title }}
    <span class="entry-date">
     <time datetime="{{ post.date | date_to_xmlschema }}">
      {{ post.date | date: "%B %d, %Y" }}
     </time>
    </span>
    {% if post.excerpt %}
     <span class="excerpt">
      {{ post.excerpt }}
      <a href="{{ site.url }}{{ post.url }}" class="more">Continue reading</a>
     </span>
    {% endif %}
   </a>
  </article>
 </li>
{% endfor %}
</ul>
